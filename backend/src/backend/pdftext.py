"""Reading a stored PDF: its text, and where a quote sits on the page.

This is what lets the assistant highlight something. It can quote a passage but
has no idea where that passage is drawn, so the server finds it: PyMuPDF
searches the page and returns the rectangles, which are stored exactly as a
highlight made by hand would be.

Rectangles are stored as fractions of the page (0–1) rather than points, so a
highlight lands correctly at any zoom or render width. The page size in points
is stored alongside, so an export can convert back to absolute units.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from . import db

MAX_PAGES_SCANNED = 400


def pdf_path(conn: sqlite3.Connection, paper_id: int) -> Path | None:
    row = conn.execute("SELECT pdf_path FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not row or not row["pdf_path"]:
        return None
    path = db.library_root() / row["pdf_path"]
    return path if path.exists() else None


def _normalise(text: str) -> str:
    """Collapse whitespace so a quote spanning a line break still matches."""
    return re.sub(r"\s+", " ", text or "").strip()


def join_broken_words(text: str) -> str:
    """Undo hyphenation left by the typesetter's line breaks.

    A justified column breaks words at the margin, and extracting the text
    keeps the hyphen: a passage comes out reading "K-means clus- tering ... no
    hyper- parameters". That is the page's layout, not the author's words, and
    it should not be what a highlight stores.

    The rule is a hyphen followed by a break and a lowercase letter. A compound
    that happens to break at its own hyphen ("state-of-the-" / "art") is joined
    wrongly, which is the accepted cost of doing this without a dictionary; the
    line-aware version below is used wherever the layout is still available.
    """
    return re.sub(r"(\w)-\s+(?=[^\W\dA-Z_])", r"\1", text or "")


# Greek letters spelled out. A model quoting an equation writes TeX ($\eta$)
# where the page has the glyph (η); spelling both out makes them the same word.
# NFKD has already turned the variant forms (ϵ, ϑ, ϕ, µ, math italics) into
# these by the time this applies.
_GREEK = str.maketrans({
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "o", "π": "pi",
    "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
})


# Another broken text layer: maths set in a Symbol/MathPi font whose codes
# land on Latin-1 fractions, so "n = 20" is extracted as "n ¼ 20" and "[" as
# "½" (Nature and Elsevier PDFs, among them three in one test library). NFKD
# would expand each into digits ("14", "12") that no quote contains; dropped
# instead, like every other symbol. A real fraction is dropped on both sides
# of a comparison, so it still matches itself.
_MATH_GLYPHS = str.maketrans({"¼": " ", "½": " ", "¾": " "})


def _mend_math_letters(text: str) -> str:
    """Undo a common broken text layer: math italics truncated to 16 bits.

    TeX's italic letters live at U+1D400–U+1D7FF. Some PDFs map them with a
    ToUnicode table that drops the top bit, so an italic l (U+1D459) is
    extracted as the Hangul syllable 푙 (U+D459), and a quote that types "l"
    cannot match it. Shifting the range back recovers the letter, which NFKD
    then turns into a plain one. Real Hangul in that range is changed too, but
    identically on both sides of a comparison, so matches are unaffected.
    """
    return "".join(
        chr(ord(c) + 0x10000) if "퐀" <= c <= "퟿" else c for c in text
    )


# What a number says has to survive folding. Dropping every symbol is right for
# words and wrong for numbers: "1.36", "13.6" and "136" all folded to "136",
# "-0.8" to "08", and "p < 0.1" to the same as "p > 0.1", so a quote that
# changed a result still matched. So three marks are kept: a decimal point, a
# minus sign in front of a number, and a comparison. A hyphen, a range dash
# ("10–20") and an exponent's sign ("10^{-5}", which extraction spells "10−5")
# are not signs and still drop out. Every rule looks only within a word or at
# the space before it, so a page folded word by word and a quote folded whole
# come out the same.
_ARROW = re.compile(r"<[-=]+>|[-=]+>|<-+(?![\d.])")
_NUMBER_MARK = re.compile(
    r"(?:(?<=\d)|(?<![\w.]))[.·](?=\d)"     # 1.36, .05
    r"|(?<![\w)\]}{^.])[-−–](?=\.?\d)"      # -0.8, (−3), r = –2
    r"|[<>≤≥≪≫]"
)
# Stand-ins that survive the letters-and-digits filter: fold has lowercased
# everything by then, so no capital can be mistaken for one.
_MARKS = {
    ".": "P", "·": "P", "-": "M", "−": "M", "–": "M",
    "<": "L", "≤": "L", "≪": "L", ">": "G", "≥": "G", "≫": "G",
}
_SHOW_MARKS = str.maketrans("PMLG", ".-<>")


def fold(text: str) -> str:
    """Lowercase alphanumerics, and the marks that change a number — the form quotes are matched in.

    Typeset text and quoted text disagree in ways that carry no meaning. A word
    broken across a line is stored with its hyphen ("hyper-" / "parameters"),
    quotation marks are curly in one and straight in the other, an en-dash
    stands in for a hyphen, accents may be composed or not. Dropping everything
    that is not a letter or a digit removes all of it at once, and closing the
    gaps is exactly what makes a hyphenated break match the joined word. The
    exceptions are a number's point, sign and comparison: see _NUMBER_MARK.
    """
    text = unicodedata.normalize("NFKD", _mend_math_letters(text or "").translate(_MATH_GLYPHS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _ARROW.sub(" ", text.lower().translate(_GREEK))
    text = _NUMBER_MARK.sub(lambda m: _MARKS[m[0]], text)
    return re.sub(r"[^a-z0-9PMLG]+", "", text).translate(_SHOW_MARKS)


def extract_text(path: Path, max_pages: int = MAX_PAGES_SCANNED) -> list[dict[str, Any]]:
    """Page text, for search and for answering questions about the document."""
    import pymupdf

    out: list[dict[str, Any]] = []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            if number > max_pages:
                break
            out.append(
                {
                    "page": number,
                    "text": page.get_text("text"),
                    "width": page.rect.width,
                    "height": page.rect.height,
                }
            )
    return out


def outline(path: Path) -> list[dict[str, Any]]:
    """The document's own table of contents, when it has one.

    Most PDFs produced by LaTeX carry a real outline with nested headings and
    page numbers. It is exact structure for almost nothing — no text
    extraction, no guessing from font sizes — so it is the cheapest way to know
    what a paper contains before reading any of it.
    """
    import pymupdf

    with pymupdf.open(path) as doc:
        return [
            {"level": level, "title": (title or "").strip(), "page": page}
            for level, title, page in doc.get_toc()
            if (title or "").strip() and page > 0
        ]


def _join_words(words: list[Any], first: int, last: int) -> str:
    """Words back into a sentence, mending anything broken across a line.

    More exact than the text-only rule: the words carry their line number, so a
    hyphen is only dropped when the break really is a line break rather than
    part of the word.
    """
    out: list[str] = []
    for i in range(first, last + 1):
        word = words[i][4]
        nxt = words[i + 1] if i < last else None
        broke_here = (
            nxt is not None
            and word.endswith("-")
            and len(word) > 1
            # (block, line): a different line is a real break.
            and (words[i][5], words[i][6]) != (nxt[5], nxt[6])
            and nxt[4][:1].islower()
        )
        if broke_here:
            out.append(word[:-1])          # no hyphen, no space: one word
        else:
            out.append(word + (" " if nxt is not None else ""))
    return "".join(out).strip()


def _index_words(texts: Iterable[str]) -> tuple[str, list[int]]:
    """Words folded into one string, and which word each character came from."""
    parts: list[str] = []
    owner: list[int] = []
    for i, text in enumerate(texts):
        folded = fold(text)
        if not folded:
            continue
        parts.append(folded)
        owner.extend([i] * len(folded))
    return "".join(parts), owner


def _page_index(page: Any) -> tuple[list[Any], str, list[int]]:
    """A page's words, their text folded into one string, and who owns each character."""
    words = page.get_text("words")      # (x0, y0, x1, y1, word, block, line, no)
    hay, owner = _index_words(w[4] for w in words)
    return words, hay, owner


def find_quote(
    path: Path, quote: str, page_hint: int | None = None
) -> dict[str, Any] | None:
    """Locate a quote and return its rectangles as page fractions.

    Matching is done on folded text against the page's own words, rather than
    by searching for the literal string. A literal search fails on anything the
    typesetter did that the quoter did not reproduce — most often a word broken
    across a line, where the PDF holds "hyper-" and "parameters" while the quote
    says "hyperparameters", and no amount of whitespace tolerance closes that.

    Folding both sides to bare alphanumerics makes them comparable, and the
    per-word positions are still available to turn a match back into rectangles.
    """
    import pymupdf

    wanted = fold(quote)
    if len(wanted) < 8:
        return None                     # too short to identify a passage

    with pymupdf.open(path) as doc:
        order = list(range(len(doc)))
        if page_hint and 1 <= page_hint <= len(doc):
            # Look where the caller expects it first; fall back to the rest.
            order.remove(page_hint - 1)
            order.insert(0, page_hint - 1)

        for index in order[:MAX_PAGES_SCANNED]:
            page = doc[index]
            words, hay, owner = _page_index(page)
            if not hay:
                continue

            at = hay.find(wanted)
            length = len(wanted)
            if at == -1:
                # A quote running past the end of a column or onto the next
                # page will not match whole; an opening long enough to be
                # unambiguous still locates it.
                if length <= 80:
                    continue
                at = hay.find(wanted[:80])
                if at == -1:
                    continue
                length = 80

            first = owner[at]
            last = owner[min(at + length, len(owner)) - 1]
            rects = [pymupdf.Rect(*words[i][:4]) for i in range(first, last + 1)]

            width, height = page.rect.width, page.rect.height
            return {
                "page": index + 1,
                "width": width,
                "height": height,
                # What the document actually says, which is what should be
                # stored: it may differ from the quote in exactly the ways the
                # fold ignored.
                "text": _join_words(words, first, last),
                "rects": _merge_by_line(rects, width, height),
            }
    return None


@dataclass(frozen=True)
class _Page:
    words: list[Any]
    hay: str
    owner: list[int]
    width: float
    height: float


@lru_cache(maxsize=24)
def _indexed(path: str, mtime_ns: int, size: int) -> tuple[_Page, ...]:
    """Every page's folded text, kept between calls.

    Checking an answer means locating several quotes in the same few files, and
    extracting a paper's words costs far more than searching them. The cache is
    keyed on the file's size and modification time as well as its path, so a
    replaced PDF is read afresh rather than matched against the old one.
    """
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc):
            if index >= MAX_PAGES_SCANNED:
                break
            words, hay, owner = _page_index(page)
            out.append(_Page(words, hay, owner, page.rect.width, page.rect.height))
    return tuple(out)


def _pages(path: Path) -> tuple[_Page, ...]:
    stat = path.stat()
    return _indexed(str(path), stat.st_mtime_ns, stat.st_size)


# An elision inside a quote: "…", "...", "[...]" or "[…]".
_ELISION = re.compile(r"\s*(?:\[\s*(?:…|(?:\.\s*){3})\s*\]|…|\.{3,})\s*")

#: Folded characters a quote needs before finding it means anything. "we show"
#: occurs in every paper; a clause or more identifies a passage.
MIN_QUOTE = 20

#: Folded characters each piece of an elided quote needs of its own. Without
#: it, "We train … the network … reaches …" stitches a claim together out of
#: words found in any paper.
MIN_PIECE = 12

#: Folded characters an ellipsis may stand for: a clause, or a sentence or two.
#: Pieces further apart than that are separate passages, and joining them
#: makes a sentence the paper does not contain. The longest skip among the
#: eval runs' quotes was 154; one that joined a paragraph to a figure caption
#: 2000 characters on is what this stops.
MAX_ELIDED = 200

#: Words that reverse a statement. A quote must hold as many as the passage it
#: matches, so an ellipsis cannot drop the "no" from "has no obvious way", and
#: a quote cannot stop at the "can" of "cannot".
_NEGATION = re.compile(
    r"\b(?:no|not|never|nor|neither|none|nothing|nobody|nowhere|without|cannot)\b|n[’']t\b",
    re.I,
)

_NUMERIC = "0123456789."


def quote_segments(quote: str) -> list[str]:
    """A quote's folded pieces, split where it elides words."""
    return [s for s in (fold(part) for part in _ELISION.split(quote or "")) if s]


def too_short(quote: str) -> bool:
    """Whether a quote, or any piece of it around an ellipsis, is too short to place."""
    return _too_short(quote_segments(quote))


def _too_short(segments: list[str]) -> bool:
    if not segments or sum(len(s) for s in segments) < MIN_QUOTE:
        return True
    return len(segments) > 1 and min(len(s) for s in segments) < MIN_PIECE


# A JSON escape copied out of a tool result: models read page text as JSON, so
# a line break reaches them as the two characters \n and is sometimes quoted
# that way. An escape is not content.
_ESCAPE = re.compile(r"\\[nrt]")


def quote_variants(quote: str) -> list[str]:
    """The quote as written, then with copied escapes read as the spaces they were.

    Both are tried rather than unescaping outright, because TeX in a quote can
    look like an escape: "\\nu" is a Greek letter, not a line break and "u".
    """
    unescaped = _ESCAPE.sub(" ", quote or "")
    return [quote] if unescaped == quote else [quote, unescaped]


def _place(
    hay: str, owner: list[int], words: list[str], segments: list[str], quote: str,
    begin_before: int | None = None,
) -> list[tuple[int, int]] | None:
    """Where each piece of a quote sits in `hay`, in order; None if it is not there.

    `hay` is folded text and `owner[i]` the index in `words` of the word its
    i-th character came from. The first piece must begin before `begin_before`
    and each later one within MAX_ELIDED of the piece before. No piece may
    begin or end inside a number, and the words covered, skipped ones
    included, must hold as many negations as the quote.
    """
    negations = len(_NEGATION.findall(quote))

    def cuts_number(a: int, b: int) -> bool:
        # "1.3" is not "1.36", nor is "36" or "0.8" what "1.36" or "-0.8" says.
        starts_inside = (
            a > 0 and owner[a - 1] == owner[a]
            and hay[a] in _NUMERIC and hay[a - 1] in _NUMERIC + "-"
        )
        ends_inside = (
            b < len(hay) and owner[b] == owner[b - 1]
            and hay[b - 1] in _NUMERIC and hay[b] in _NUMERIC
        )
        return starts_inside or ends_inside

    def same_negations(spans: list[tuple[int, int]]) -> bool:
        covered = words[owner[spans[0][0]]:owner[spans[-1][1] - 1] + 1]
        return len(_NEGATION.findall(join_broken_words(" ".join(covered)))) == negations

    def place(done: list[tuple[int, int]]) -> list[tuple[int, int]] | None:
        if len(done) == len(segments):
            return done if same_negations(done) else None
        segment = segments[len(done)]
        after = done[-1][1] if done else 0
        until = after + MAX_ELIDED + 1 if done else (begin_before or len(hay))
        at = hay.find(segment, after)
        while at != -1 and at < until:
            span = (at, at + len(segment))
            if not cuts_number(*span):
                found = place(done + [span])
                if found is not None:
                    return found
            at = hay.find(segment, at + 1)
        return None

    return place([])


def locate_quote(
    path: Path, quote: str, page_hint: int | None = None
) -> dict[str, Any] | None:
    """Find a quote word for word, or report that the document does not say it.

    This is the check behind a citation, so unlike `find_quote` it never settles
    for part of the passage: every word must be there, in order. The only
    allowances are the ones that carry no meaning — typesetting, punctuation,
    case, hyphenation, TeX for a Greek letter — plus an ellipsis, which may skip
    a little text but not reorder it, change a number, or drop a "not". A
    quote may run from one page onto the next, since a sentence often does.

    Returns the page it starts on, the rectangles to draw there, and the text as
    the document has it; None when it is not found or too short to identify a
    passage.
    """
    for variant in quote_variants(quote):
        hit = _locate(path, quote_segments(variant), page_hint, variant)
        if hit is not None:
            return hit
    return None


def _locate(
    path: Path, segments: list[str], page_hint: int | None, quote: str
) -> dict[str, Any] | None:
    if _too_short(segments):
        return None

    pages = _pages(path)
    order = list(range(len(pages)))
    if page_hint and 1 <= page_hint <= len(pages):
        order.remove(page_hint - 1)
        order.insert(0, page_hint - 1)

    for index in order:
        page = pages[index]
        following = pages[index + 1] if index + 1 < len(pages) else None
        split = len(page.hay)
        hay = page.hay + (following.hay if following else "")
        if not -1 < hay.find(segments[0]) < split:      # it must begin on this page
            continue

        owner, words = page.owner, [w[4] for w in page.words]
        if following is not None:
            owner = owner + [i + len(words) for i in following.owner]
            words = words + [w[4] for w in following.words]
        spans = _place(hay, owner, words, segments, quote, begin_before=split)
        if spans is not None:
            return _located(index, page, following, split, spans)
    return None


def _located(
    index: int, page: _Page, following: _Page | None, split: int,
    spans: list[tuple[int, int]],
) -> dict[str, Any]:
    """Turn matched character spans back into words, rectangles and text."""
    here: list[int] = []
    after: list[int] = []
    for a, b in spans:
        for c in range(a, b):
            if c < split:
                here.append(page.owner[c])
            elif following is not None:
                after.append(following.owner[c - split])

    import pymupdf

    parts: list[str] = []
    for words, picked in ((page.words, here), (following.words if following else [], after)):
        if picked:
            parts.append(_join_words(words, min(picked), max(picked)))
    rects = [pymupdf.Rect(*page.words[i][:4]) for i in sorted(set(here))]
    return {
        "page": index + 1,
        "width": page.width,
        "height": page.height,
        "text": " ".join(parts),
        "rects": _merge_by_line(rects, page.width, page.height),
        "continues": bool(after),
    }


def contains_quote(text: str, quote: str) -> bool:
    """The same test as `locate_quote`, against plain text such as an abstract."""
    words = (text or "").split()
    hay, owner = _index_words(words)
    for variant in quote_variants(quote):
        segments = quote_segments(variant)
        if not _too_short(segments) and _place(hay, owner, words, segments, variant) is not None:
            return True
    return False


@lru_cache(maxsize=256)
def _sha256(path: str, mtime_ns: int, size: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    """The file's SHA-256: which exact PDF an answer or an index was built from."""
    stat = path.stat()
    return _sha256(str(path), stat.st_mtime_ns, stat.st_size)


def _merge_by_line(rects: list[Any], width: float, height: float) -> list[dict[str, float]]:
    """One rectangle per line, as fractions of the page.

    A match spanning several lines comes back as several boxes, and a match
    crossing a span boundary can produce overlapping ones. Overlaps matter
    because marks are drawn with multiply blending, where drawing twice looks
    twice as dark.
    """
    rows: list[list[Any]] = []
    for r in sorted(rects, key=lambda r: (r.y0, r.x0)):
        centre = (r.y0 + r.y1) / 2
        row = next(
            (
                group
                for group in rows
                if abs(centre - (group[0].y0 + group[0].y1) / 2) < (group[0].y1 - group[0].y0) * 0.6
            ),
            None,
        )
        if row is not None:
            row.append(r)
        else:
            rows.append([r])

    out = []
    for row in rows:
        x0 = min(r.x0 for r in row)
        x1 = max(r.x1 for r in row)
        y0 = min(r.y0 for r in row)
        y1 = max(r.y1 for r in row)
        out.append(
            {"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}
        )
    return out


def search_text(path: Path, query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Passages matching a query, with the page they are on."""
    needle = _normalise(query).lower()
    if not needle:
        return []

    hits: list[dict[str, Any]] = []
    for page in extract_text(path):
        haystack = _normalise(page["text"])
        low = haystack.lower()
        start = low.find(needle)
        while start != -1 and len(hits) < limit:
            hits.append(
                {
                    "page": page["page"],
                    # Enough either side to judge the passage in context.
                    "excerpt": haystack[max(0, start - 160) : start + len(needle) + 160],
                }
            )
            start = low.find(needle, start + len(needle))
        if len(hits) >= limit:
            break
    return hits
