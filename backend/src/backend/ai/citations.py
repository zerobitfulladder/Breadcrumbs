"""Checking what an answer says a paper says.

The assistant cites a claim about a paper with a short exact quote:

    The goodness is [p. 3](cite:128 "the sum of the squared activities")

After it answers, every such quote is looked for in that paper: word for word
in the stored PDF, or in the abstract when there is no PDF. Nothing here asks a
model anything. A quote is found or it is not, and the same answer checked
twice gets the same verdict.

What this does and does not establish matters, because the UI states it:

  * A found quote proves the words are in the paper, on the page shown. It
    does not prove they support the sentence they are attached to. That is for
    the reader to judge, and the citation takes them to the passage to do it.
  * A quote that is not found is shown as unverified, never hidden. The
    assistant gets one chance to correct its quotes before the answer is shown.
  * Prose with no citation is the model's own explanation. It is allowed, and
    the answer says how much of it was checked rather than implying all of it.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import pdftext

#: `[label](cite:ID "quote")`. The quote may use curly or straight quotation
#: marks, a page hint may follow the id (`cite:12#p4`), and the quote is lazy
#: up to the closing mark that precedes `)`, so a quotation mark inside the
#: quote does not end it early.
CITE = re.compile(
    r"\[(?P<label>[^\[\]\n]{0,80})\]\(\s*cite:(?P<paper>\d+)(?:\s*[#:/,]\s*p?\.?\s*(?P<page>\d+))?"
    r"(?:\s+[\"“”'‘’](?P<quote>.+?)[\"“”'‘’])?\s*\)"
)

#: Verdicts, and whether each counts against the answer.
VERIFIED = "verified"
NOT_FOUND = "not_found"            # checked, and the paper does not say it
UNKNOWN_PAPER = "unknown_paper"    # no paper has that id
TOO_SHORT = "too_short"            # a few words identify nothing
NO_QUOTE = "no_quote"              # a citation with nothing to check
NO_SOURCE = "no_source"            # neither a PDF nor an abstract to check against

FAILED = {NOT_FOUND, UNKNOWN_PAPER}
UNCHECKED = {TOO_SHORT, NO_QUOTE, NO_SOURCE}


def _check_one(
    conn: sqlite3.Connection, paper_id: int, quote: str | None, page_hint: int | None
) -> dict[str, Any]:
    out: dict[str, Any] = {"paper_id": paper_id, "quote": (quote or "").strip()}
    if page_hint:
        out["page_claimed"] = page_hint
    row = conn.execute(
        "SELECT id, title, year, abstract FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if row is None:
        return {**out, "status": UNKNOWN_PAPER}
    out["title"] = row["title"]
    out["year"] = row["year"]
    if not out["quote"]:
        return {**out, "status": NO_QUOTE}
    if sum(len(s) for s in pdftext.quote_segments(out["quote"])) < pdftext.MIN_QUOTE:
        return {**out, "status": TOO_SHORT}

    checked: list[str] = []
    path = pdftext.pdf_path(conn, paper_id)
    if path is not None:
        checked.append("pdf")
        hit = pdftext.locate_quote(path, out["quote"], page_hint)
        if hit is not None:
            return {
                **out,
                "status": VERIFIED,
                "where": "pdf",
                "page": hit["page"],
                "rects": hit["rects"],
                "matched": hit["text"],
                "pdf_sha256": pdftext.file_sha256(path),
            }
    if row["abstract"]:
        checked.append("abstract")
        if pdftext.contains_quote(row["abstract"], out["quote"]):
            return {**out, "status": VERIFIED, "where": "abstract"}
    if not checked:
        return {**out, "status": NO_SOURCE}
    return {**out, "status": NOT_FOUND, "checked": checked}


def check(conn: sqlite3.Connection, text: str) -> tuple[str, list[dict[str, Any]]]:
    """Check every citation in an answer.

    Returns the answer with each citation rewritten as `[label](citation:N)`,
    N indexing the returned list, and the list itself. The rewritten form is
    what the UI renders: a link destination with no quote in it parses the same
    in every Markdown renderer, which the model's own spelling does not.
    """
    found: list[dict[str, Any]] = []
    source = text or ""

    def replace(m: re.Match[str]) -> str:
        page = int(m["page"]) if m["page"] else None
        result = _check_one(conn, int(m["paper"]), m["quote"], page)
        result["n"] = len(found) + 1
        found.append(result)
        label = (m["label"] or "").strip() or "source"
        chip = f"[{label}](citation:{result['n']})"
        # A quote the reader cannot see does no work. Models often put the
        # substance in the quote ("backprop needs [p. 2](cite:…)"), leaving
        # the sentence with a chip where the fact should be. Unless the quoted
        # words already stand just before the citation, they are shown.
        quote = result["quote"]
        before = source[max(0, m.start() - 600):m.start()]
        if not quote or pdftext.contains_quote(before, quote):
            return chip
        return f"“{_escape(quote)}” {chip}"

    return held_papers_plain(conn, CITE.sub(replace, source)), found


#: A suggestion link, `[title](paper:identifier)`, which the panel renders as a
#: button offering to add the paper.
PAPER_LINK = re.compile(r"\[(?P<label>[^\[\]\n]+)\]\(paper:(?P<ident>[^)\s]+)\)")


def held_papers_plain(conn: sqlite3.Connection, text: str) -> str:
    """Suggestion links to papers already in the library, as plain titles.

    Models sometimes use the suggestion syntax for any paper they mention, so
    a paper the user holds would be offered as one to add. That misstates the
    library, which is exactly what labels are for avoiding.
    """
    from .. import ids

    def replace(m: re.Match[str]) -> str:
        ident = m["ident"]
        row = conn.execute(
            "SELECT 1 FROM papers WHERE doi = ? OR arxiv_id = ? OR openalex_id = ?",
            (ids.norm_doi(ident), ids.norm_arxiv(ident), ids.norm_openalex(ident)),
        ).fetchone()
        return f"**{m['label']}**" if row else m.group(0)

    return PAPER_LINK.sub(replace, text)


def _escape(text: str) -> str:
    """A quote as literal Markdown: no emphasis, links, code or maths from it."""
    return re.sub(r"([\\`*_\[\]$<>])", r"\\\1", " ".join(text.split()))


def summary(citations: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(citations),
        "verified": sum(c["status"] == VERIFIED for c in citations),
        "failed": sum(c["status"] in FAILED for c in citations),
        "unchecked": sum(c["status"] in UNCHECKED for c in citations),
    }


def repair_prompt(citations: list[dict[str, Any]]) -> str:
    """What the assistant is told when some of its quotes were not found."""
    lines = []
    for c in citations:
        if c["status"] == NOT_FOUND:
            lines.append(
                f"- [{c['n']}] paper {c['paper_id']}: \"{c['quote']}\" is not in the paper's "
                f"{' or '.join(c.get('checked') or ['text'])}."
            )
        elif c["status"] == UNKNOWN_PAPER:
            lines.append(f"- [{c['n']}] there is no paper with id {c['paper_id']}.")
        elif c["status"] == TOO_SHORT:
            lines.append(f"- [{c['n']}] \"{c['quote']}\" is too short to identify a passage.")
    return (
        "Citation check (automatic, not from the user): some quotes in your answer could not "
        "be found word for word.\n"
        + "\n".join(lines)
        + "\n\nFind the exact wording with search_pdf, search_passages or read_pdf and fix each "
        "quote, or remove the citation and say plainly that the claim is unverified or comes "
        "from your own knowledge. Do not cite anything you have not seen in a tool result or "
        "the page text this turn. Then give the complete corrected answer; it replaces the "
        "previous one."
    )


def needs_repair(citations: list[dict[str, Any]]) -> bool:
    return any(c["status"] in FAILED or c["status"] == TOO_SHORT for c in citations)


#: Language that attributes a statement to a paper: "the authors report",
#: "per Appendix B", "Table 2 shows". A rule, so it misses some phrasings and
#: catches some innocent ones; it only decides whether to ask once for quotes.
ATTRIBUTION = re.compile(
    r"\b(?:the|this|that) (?:paper|authors?|study|article|work|preprint)\b"
    r"|\b(?:they|authors) (?:report|use|show|find|found|state|propose|define|train|set|"
    r"measure|argue|claim|note|observe|conclude|demonstrate|introduce)\w*"
    r"|\b(?:appendix|section|table|figure|fig\.|eq\.|equation)\s*\(?[a-z]?\d"
    # A page reference: "(p. 1)", "pp. 3-4", "page 5".
    r"|\bp{1,2}\.\s*\d|\bpages?\s+\d",
    re.I,
)


_NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|none|nowhere|cannot)\b|n['’]t\b", re.I)
#: A sentence ends at . ! or ? before a capital or an opening quote, so "(p. 1)",
#: "Fig. 2" and "e.g. this" stay whole.
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘(\[])|\n+")


def attributes_without_citing(text: str, citations: list[dict[str, Any]]) -> bool:
    """An answer that says what a paper says, and cites nothing for it.

    Only positive attributions count. "The paper does not report this" is the
    right answer to some questions, and no quote can support an absence; asking
    for one would push the model to cite something nearby as if it answered.
    """
    if citations:
        return False
    return any(
        ATTRIBUTION.search(sentence) and not _NEGATION.search(sentence)
        for sentence in _SENTENCES.split(text or "")
    )


def uncited_prompt() -> str:
    return (
        "Citation check (automatic, not from the user): your answer attributes statements "
        "to a paper (what it reports, uses, or shows) but cites none of them, so none could "
        "be checked. For each statement about what a paper says, add a citation with an "
        "exact quote from text you have seen this turn, [p. N](cite:ID \"exact words\"), "
        "finding the wording with search_pdf, search_passages or read_pdf if you need to. "
        "Statements that are your own explanation stay uncited; word them as yours. Then "
        "give the complete answer again; it replaces the previous one. A statement that the "
        "paper does not contain something needs no quote."
    )
