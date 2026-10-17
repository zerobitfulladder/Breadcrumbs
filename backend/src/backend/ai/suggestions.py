"""Checking the papers an answer suggests.

A suggestion is a link, `[title](paper:identifier)`, which the panel renders as
a button that opens the paper's preview. The model writes both halves, and a
model will write a plausible identifier from memory: asked for a follow-up to
feedback alignment, one wrote the arXiv id of a paper on Chern-Simons theory.
The preview would have shown the real record before anything was saved, but
the suggestion itself said something false.

So each identifier is looked up (a single OpenAlex record, which is free) and
the title it really has is compared with the title the answer gives it. A
suggestion whose identifier is another paper loses its button, says what the
identifier really is, and counts toward the turn's one correction round, the
same as a quote that was not found.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any, Awaitable, Callable

import httpx

from .. import db, ids, pdftext
from .citations import PAPER_LINK

MATCH = "matches"
MISMATCH = "mismatch"        # the identifier is a different paper
UNCHECKED = "unchecked"      # not found, or an identifier OpenAlex cannot take

Resolver = Callable[[str], Awaitable[dict[str, Any] | None]]

_STOP = frozenset("a an and as at by for from in into is of on or the to with via".split())


def _words(text: str) -> set[str]:
    words = (pdftext.fold(w) for w in re.split(r"[\s\-–—:,.;()]+", text or ""))
    return {w for w in words if w and w not in _STOP}


def same_paper(label: str, record: dict[str, Any]) -> bool:
    """Whether a link's text names the record: its title, or first author and year.

    Measured against the shorter side, so a short label ("Direct Feedback
    Alignment") matches the full title, and "Nøkland 2016" matches by first
    author and year.
    """
    said = _words(label)
    if not said:
        return False
    title = _words(record.get("title") or "")
    names = [(a.get("name") or "").split() for a in record.get("authors") or []]
    surname = _words(names[0][-1]) if names and names[0] else set()
    year = {str(record["year"])} if record.get("year") else set()

    def covers(known: set[str]) -> bool:
        return bool(known) and len(said & known) / min(len(said), len(known)) >= 0.6

    return covers(title) or covers(surname | year)


def openalex_resolver(client: httpx.AsyncClient, email: str) -> Resolver:
    """Look an identifier up in OpenAlex: one record, no references fetched."""
    from ..sources import openalex

    async def resolve(identifier: str) -> dict[str, Any] | None:
        doi = ids.norm_doi(identifier)
        arxiv = ids.norm_arxiv(identifier) if not doi else None
        try:
            if doi:
                return await openalex.fetch_by_doi(client, doi, email)
            if arxiv:
                return await openalex.fetch_by_doi(client, f"10.48550/arxiv.{arxiv}", email)
            work = ids.norm_openalex(identifier)
            if work and work.upper().startswith("W"):
                return await openalex.fetch_by_id(client, work, email)
        except Exception:          # a source failure leaves it unchecked, not failed
            return None
        return None

    return resolve


async def check(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient | None,
    text: str,
    resolve: Resolver | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Check every suggestion in an answer, and rewrite the ones that fail.

    Runs on text whose citations are already checked, so links to papers the
    library holds have become plain titles and only true suggestions remain.
    """
    links = list(PAPER_LINK.finditer(text or ""))
    if not links:
        return text, []
    if resolve is None:
        if client is None:
            return text, []
        resolve = openalex_resolver(client, db.get_setting(conn, "contact_email").strip())

    records = await asyncio.gather(*(resolve(m["ident"]) for m in links))
    results: list[dict[str, Any]] = []
    out: list[str] = []
    last = 0
    for m, record in zip(links, records):
        label, ident = m["label"], m["ident"]
        if record is None:
            status, shown = UNCHECKED, m.group(0)
        elif same_paper(label, record):
            status, shown = MATCH, m.group(0)
        else:
            status = MISMATCH
            shown = (
                f"**{label}** (not offered: its identifier `{ident}` is a different "
                f"paper, “{record.get('title')}”)"
            )
        results.append({
            "label": label, "identifier": ident, "status": status,
            "resolved_title": (record or {}).get("title"),
        })
        out.append(text[last:m.start()])
        out.append(shown)
        last = m.end()
    out.append(text[last:])
    return "".join(out), results


def repair_lines(results: list[dict[str, Any]]) -> list[str]:
    return [
        f"- The suggested paper “{r['label']}” has identifier {r['identifier']}, which is a "
        f"different paper: “{r['resolved_title']}”. Find its real identifier with "
        "lookup_identifier or web_search, or leave the suggestion out."
        for r in results
        if r["status"] == MISMATCH
    ]
