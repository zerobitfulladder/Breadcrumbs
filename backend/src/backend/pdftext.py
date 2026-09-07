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

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

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


def fold(text: str) -> str:
    """Lowercase alphanumerics only — the form quotes are matched in.

    Typeset text and quoted text disagree in ways that carry no meaning. A word
    broken across a line is stored with its hyphen ("hyper-" / "parameters"),
    quotation marks are curly in one and straight in the other, an en-dash
    stands in for a hyphen, accents may be composed or not. Dropping everything
    that is not a letter or a digit removes all of it at once, and closing the
    gaps is exactly what makes a hyphenated break match the joined word.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


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


def _page_index(page: Any) -> tuple[list[Any], str, list[int]]:
    """A page's words, their text folded into one string, and who owns each character."""
    words = page.get_text("words")      # (x0, y0, x1, y1, word, block, line, no)
    parts: list[str] = []
    owner: list[int] = []
    for i, word in enumerate(words):
        folded = fold(word[4])
        if not folded:
            continue
        parts.append(folded)
        owner.extend([i] * len(folded))
    return words, "".join(parts), owner


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
