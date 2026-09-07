"""Re-fetch every paper's reference list, in full, and rebuild the links.

Reference lists gathered under the old per-paper cap are truncated: the cap
applied to both directions with one number, and a bibliography that ran past it
simply lost its tail. Removing the cap does not repair what was already stored,
so this walks the library once and refetches each paper's references complete.

Run it after a schema change that widens what gets stored, or any time a run of
adds happened while a source was throttling:

    uv run --project backend python -m backend.backfill          # the library
    uv run --project backend python -m backend.backfill --dry-run
    uv run --project backend python -m backend.backfill --paper 47

Storing is additive — `store_pending` skips identifiers already recorded — so
re-running is safe and only ever fills gaps.
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from typing import Any

from . import db, ids, store
from .sources import openalex, s2
from .sources.http import SourceError, make_client

#: OpenAlex asks for no more than 10 requests/second in the polite pool. One
#: paper costs a work lookup plus a batch call per 50 references, so a short
#: pause between papers keeps a whole-library run well inside that.
_PAUSE = 0.2


async def references_for(
    client: Any, paper: sqlite3.Row, email: str, s2_key: str
) -> tuple[list[dict[str, Any]], str]:
    """Every paper `paper` cites, with a one-line account of where they came from.

    OpenAlex first: it holds the full `referenced_works` list on the record, so
    the count is known before the details are fetched and a short list is
    visible as a short list rather than as a silent truncation. Semantic
    Scholar is the fallback for the rare work OpenAlex does not hold.
    """
    oa_id = paper["openalex_id"]
    note = "no OpenAlex id"
    if oa_id:
        try:
            work = await openalex.fetch_by_id(client, oa_id, email)
            refs = (work or {}).get("referenced_works") or []
            if refs:
                works = await openalex.fetch_works_batch(client, refs, email)
                records = [
                    {
                        "doi": w.get("doi"),
                        "openalex_id": w.get("openalex_id"),
                        "s2_id": None,
                        "arxiv_id": w.get("arxiv_id"),
                        "title": w.get("title"),
                        "year": w.get("year"),
                        "authors_blob": ", ".join(
                            a["name"] for a in (w.get("authors") or [])[:5] if a.get("name")
                        ) or None,
                        "citation_count": w.get("citation_count"),
                        "context": None, "intent": None, "is_influential": 0,
                    }
                    for w in works
                ]
                return records, f"OpenAlex {len(records)}/{len(refs)}"
            # OpenAlex holds the record but lists no bibliography for it. That
            # is a gap in its coverage, not a paper without references, so it
            # falls through to Semantic Scholar the same way a failure does.
            note = "OpenAlex lists no references"
        except SourceError as exc:
            note = f"OpenAlex failed ({exc})"

    ident = paper["s2_id"]
    doi = ids.norm_doi(paper["doi"])
    if not ident and doi:
        ident = f"DOI:{doi}"
    if ident:
        try:
            found = await s2.fetch_references(client, ident, s2_key)
            return found, f"{note}; Semantic Scholar {len(found)}"
        except SourceError as exc:
            return [], f"{note}; Semantic Scholar failed ({exc})"
    return [], note


async def backfill(paper_id: int | None = None, dry_run: bool = False) -> None:
    with db.session() as conn:
        settings = db.get_settings(conn)
        email = settings.get("contact_email", "")
        s2_key = settings.get("semantic_scholar_key", "")
        sql = (
            "SELECT id, title, doi, s2_id, openalex_id, reference_count, "
            "  (SELECT COUNT(*) FROM pending_links pl WHERE pl.from_paper_id = papers.id) AS held "
            "FROM papers"
        )
        args: list[Any] = []
        if paper_id is not None:
            sql += " WHERE id = ?"
            args.append(paper_id)
        papers = conn.execute(sql + " ORDER BY id", args).fetchall()

    if not email:
        print("warning: no contact email set — OpenAlex will throttle harder.\n")

    total_added = 0
    async with make_client() as client:
        for n, paper in enumerate(papers, 1):
            title = (paper["title"] or "")[:48]
            records, note = await references_for(client, paper, email, s2_key)
            before = paper["held"]

            if dry_run:
                print(
                    f"[{n}/{len(papers)}] {paper['id']:>4} {title:<48} "
                    f"{before:>4} held · {note}",
                    flush=True,
                )
            else:
                with db.session() as conn:
                    added = store.store_pending(conn, paper["id"], records)
                total_added += added
                flag = " +" if added else "  "
                print(
                    f"[{n}/{len(papers)}] {paper['id']:>4} {title:<48} "
                    f"{before:>4} -> {before + added:<4}{flag}{added:<4} {note}",
                    flush=True,
                )
            await asyncio.sleep(_PAUSE)

    if dry_run:
        print("\ndry run: nothing was written.")
        return

    print(f"\n{total_added} reference rows added.")
    with db.session() as conn:
        created = store.resolve_links(conn)
    print(f"{created} new links created.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", type=int, help="only this paper id")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()
    db.init_db()
    asyncio.run(backfill(args.paper, args.dry_run))


if __name__ == "__main__":
    main()
