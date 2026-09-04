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


def find_quote(
    path: Path, quote: str, page_hint: int | None = None
) -> dict[str, Any] | None:
    """Locate a quote and return its rectangles as page fractions.

    Tries the exact string first, then a whitespace-tolerant retry, because a
    quote copied from extracted text often spans a line break that the PDF
    itself does not contain.
    """
    import pymupdf

    wanted = _normalise(quote)
    if len(wanted) < 4:
        return None

    with pymupdf.open(path) as doc:
        order = list(range(len(doc)))
        if page_hint and 1 <= page_hint <= len(doc):
            # Look where the caller expects it first; fall back to the rest.
            order.remove(page_hint - 1)
            order.insert(0, page_hint - 1)

        for index in order[:MAX_PAGES_SCANNED]:
            page = doc[index]
            rects = page.search_for(wanted)
            if not rects:
                # PyMuPDF matches across lines only when the needle has no
                # newline; a long quote often fails, so retry on a prefix that
                # is still distinctive.
                if len(wanted) > 60:
                    rects = page.search_for(wanted[:60])
                if not rects:
                    continue

            width, height = page.rect.width, page.rect.height
            return {
                "page": index + 1,
                "width": width,
                "height": height,
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
