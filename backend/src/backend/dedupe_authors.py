"""Find and merge author rows that are the same person.

One person arrives as several rows for two reasons. OpenAlex splits prolific
authors across entities — Geoffrey Hinton has two, holding 36 and 378 works,
neither carrying an ORCID to tie them together — and `_upsert_author` matches
on id before name, so a second id makes a second row. Separately, a source may
give initials where another gave a full name ("H. B. Barlow", "Horace
Barlow"), which the name fallback cannot join either.

Candidates are surname plus compatible given names, where an initial may stand
for a full name. That is deliberately narrow: it will not join "J. Smith" to
"Jane Smith" if a "John Smith" is also present, and it never merges on surname
alone.

    uv run --project backend python -m backend.dedupe_authors --dry-run
    uv run --project backend python -m backend.dedupe_authors

Merging keeps the row with more papers, folds everything the other held into
it, and records the absorbed ids as aliases so the duplicate cannot come back.
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Any

from . import db, ids, store


def find_duplicates(conn: sqlite3.Connection) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pairs of rows that look like one person, survivor first."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT a.id, a.name, a.openalex_id, a.orcid, a.s2_author_id, "
            "  (SELECT COUNT(*) FROM paper_authors pa WHERE pa.author_id = a.id) AS papers "
            "FROM authors a ORDER BY a.id"
        )
    ]
    by_surname: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parts = ids.name_parts(row["name"])
        if not parts:
            continue
        row["given"], surname = parts
        by_surname.setdefault(surname, []).append(row)

    pairs = []
    for group in by_surname.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if not ids.compatible(a["given"], b["given"]):
                    continue
                # An ORCID is a person, not a record: two different ones are
                # two people however alike the names look.
                if a["orcid"] and b["orcid"] and a["orcid"] != b["orcid"]:
                    continue
                keep, absorb = sorted((a, b), key=lambda r: (-r["papers"], r["id"]))
                pairs.append((keep, absorb))
    return pairs


def dedupe(dry_run: bool = False) -> None:
    with db.session() as conn:
        pairs = find_duplicates(conn)

    if not pairs:
        print("No duplicate authors found.")
        return

    for keep, absorb in pairs:
        print(f"\n{keep['name']}")
        for row, role in ((keep, "keep   "), (absorb, "absorb ")):
            print(
                f"  {role} [{row['id']:>3}] {row['name']:<24} {row['papers']:>2}p  "
                f"oa={row['openalex_id'] or '-':<12} orcid={row['orcid'] or '-'}"
            )
        if dry_run:
            continue
        with db.session() as conn:
            moved = store.merge_authors(conn, keep["id"], absorb["id"])
        detail = ", ".join(f"{k}={v}" for k, v in moved.items() if v)
        print(f"  merged: {detail or 'nothing to move'}")

    print(f"\n{len(pairs)} pair(s)" + (" — dry run, nothing written." if dry_run else " merged."))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()
    db.init_db()
    dedupe(args.dry_run)


if __name__ == "__main__":
    main()
