"""Re-date papers whose stored year came from a reprint or a digitised deposit.

Sources routinely index one work several times under a single identifier. CMU
deposited a scan of ALVINN on KiltHub in 2018; OpenAlex holds only that deposit
and dates the paper 2018, while Semantic Scholar still reports the NIPS 1988
original. Merging field by field then produced records no source asserts — a
2018 paper published at NIPS, carrying the deposit's June.

`merge_records` now takes year and month together from whichever source reports
the earliest plausible year. That fixes papers added from here on; this walks
the library and applies the same rule to those already stored:

    uv run --project backend python -m backend.redate --dry-run   # report only
    uv run --project backend python -m backend.redate             # apply
    uv run --project backend python -m backend.redate --paper 118

Only the date moves. Nothing else about the record is rewritten, and a paper
whose sources agree is left alone.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from . import db, ingest
from .sources.http import make_client

#: Semantic Scholar throttles hard without a key, and it is the source most
#: likely to hold the original date, so the pace here is gentler than backfill's.
_PAUSE = 0.4


async def proposed_date(client: Any, paper: Any, email: str, s2_key: str) -> dict[str, Any]:
    """What the sources say this paper's date should be, and who said it."""
    identifier = (
        paper["doi"] or paper["arxiv_id"] or paper["openalex_id"] or paper["s2_id"]
    )
    if not identifier:
        return {"error": "no identifier"}
    parsed = ingest.parse_identifier(identifier)
    if parsed is None:
        return {"error": f"unparseable identifier {identifier}"}
    kind, value = parsed

    outcomes = await ingest._gather_sources(client, kind, value, email, s2_key, paper["title"])
    by_source = {n: o.record for n, o in outcomes.items() if o.status == "ok" and o.record}
    # A source that errored is not a source that agreed. Semantic Scholar is
    # throttled constantly without a key and is the one most likely to hold the
    # original date, so treating its absence as consent would quietly leave a
    # misdated paper looking correct — the exact failure this module exists to
    # fix. Report which sources dropped out and let the caller retry them.
    lost = sorted(o.name for o in outcomes.values() if o.status == "error")
    if not by_source:
        detail = "; ".join(
            f"{o.name} {o.status}" for o in outcomes.values() if o.status in ("error", "empty")
        )
        return {"error": f"no source resolved it ({detail})"}
    found = ingest.date_from_earliest(by_source)
    if not found:
        return {"error": "no source reported a usable year"}
    return {**found, "lost": lost}


async def redate(paper_id: int | None = None, dry_run: bool = False) -> None:
    with db.session() as conn:
        settings = db.get_settings(conn)
        email = settings.get("contact_email", "").strip()
        s2_key = settings.get("semantic_scholar_key", "").strip()
        sql = "SELECT id, title, year, month, doi, arxiv_id, openalex_id, s2_id FROM papers"
        args: list[Any] = []
        if paper_id is not None:
            sql += " WHERE id = ?"
            args.append(paper_id)
        papers = conn.execute(sql + " ORDER BY id", args).fetchall()

    changed, incomplete, skipped, failed = [], [], 0, 0
    async with make_client() as client:
        for n, paper in enumerate(papers, 1):
            title = (paper["title"] or "")[:44]
            found = await proposed_date(client, paper, email, s2_key)
            await asyncio.sleep(_PAUSE)

            if "error" in found:
                failed += 1
                print(f"[{n}/{len(papers)}] {paper['id']:>4} {title:<44} ! {found['error']}",
                      flush=True)
                continue

            year, month = found["year"], found.get("month")
            if year == paper["year"] and month == paper["month"]:
                if found.get("lost"):
                    incomplete.append(paper["id"])
                    print(
                        f"[{n}/{len(papers)}] {paper['id']:>4} {title:<44} "
                        f"? unchanged, but {', '.join(found['lost'])} did not answer",
                        flush=True,
                    )
                else:
                    skipped += 1
                continue

            spread = found.get("year_disagreement") or {}
            detail = ", ".join(f"{k} {v}" for k, v in sorted(spread.items())) or found["year_source"]
            old = f"{paper['year']}" + (f"-{paper['month']:02d}" if paper["month"] else "")
            new = f"{year}" + (f"-{month:02d}" if month else "")
            print(f"[{n}/{len(papers)}] {paper['id']:>4} {title:<44} {old:>8} -> {new:<8} [{detail}]",
                  flush=True)
            changed.append((paper["id"], year, month))

            if not dry_run:
                with db.session() as conn:
                    # A plain UPDATE, not upsert_paper: that COALESCEs, so it
                    # could never clear the deposit's month from a paper whose
                    # real date carries none.
                    conn.execute(
                        "UPDATE papers SET year = ?, month = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (year, month, paper["id"]),
                    )

    verb = "would change" if dry_run else "changed"
    print(
        f"\n{verb} {len(changed)} · unchanged {skipped} · "
        f"unverified {len(incomplete)} · could not check {failed}"
    )
    if incomplete:
        ids_ = " ".join(str(i) for i in incomplete[:20])
        print(
            f"{len(incomplete)} paper(s) had a source drop out, so their date is unconfirmed "
            f"rather than agreed. Re-run to check them again: {ids_}"
            + (" ..." if len(incomplete) > 20 else "")
        )
    if dry_run and changed:
        print("dry run: nothing was written.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", type=int, help="only this paper id")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()
    db.init_db()
    asyncio.run(redate(args.paper, args.dry_run))


if __name__ == "__main__":
    main()
