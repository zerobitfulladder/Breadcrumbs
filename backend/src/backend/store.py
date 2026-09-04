"""Persistence: upsert merged paper records and resolve links.

The link model is the point of the app. Adding a paper never creates rows for
other papers. Instead every reference and citation is stored in `pending_links`
as a set of identifiers. When the other side is later added by hand, those
rows resolve into real `links`. So the graph fills in as the library grows,
without ever auto-importing a paper you did not choose.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from . import ids

# Every column that is UNIQUE on `papers`. Leaving one out makes a duplicate
# look new, and the insert then fails on that column's constraint.
_ID_COLUMNS = ("doi", "arxiv_id", "s2_id", "openalex_id", "pmid")


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------
def find_paper_id(conn: sqlite3.Connection, rec: dict[str, Any]) -> int | None:
    """Match an incoming record against the library on any identifier."""
    keys = ids.identity_keys(rec)
    for col in _ID_COLUMNS:
        value = keys.get(col)
        if value:
            row = conn.execute(f"SELECT id FROM papers WHERE {col} = ?", (value,)).fetchone()
            if row:
                return row["id"]
    return None


# ---------------------------------------------------------------------------
# authors and institutions
# ---------------------------------------------------------------------------
def _upsert_institution(conn: sqlite3.Connection, inst: dict[str, Any]) -> int | None:
    name = inst.get("name")
    if not name:
        return None
    ror = inst.get("ror")
    oa = inst.get("openalex_id")

    for col, val in (("ror", ror), ("openalex_id", oa)):
        if val:
            row = conn.execute(f"SELECT id FROM institutions WHERE {col} = ?", (val,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE institutions SET name = COALESCE(?, name), "
                    "country_code = COALESCE(?, country_code), type = COALESCE(?, type) WHERE id = ?",
                    (name, inst.get("country_code"), inst.get("type"), row["id"]),
                )
                return row["id"]

    row = conn.execute(
        "SELECT id FROM institutions WHERE ror IS NULL AND openalex_id IS NULL AND name = ?",
        (name,),
    ).fetchone()
    if row:
        return row["id"]

    cur = conn.execute(
        "INSERT INTO institutions(ror, openalex_id, name, country_code, type) VALUES(?,?,?,?,?)",
        (ror, oa, name, inst.get("country_code"), inst.get("type")),
    )
    return cur.lastrowid


def _upsert_author(conn: sqlite3.Connection, author: dict[str, Any]) -> int | None:
    name = author.get("name")
    if not name:
        return None

    ID_COLUMNS = ("orcid", "openalex_id", "s2_author_id")

    def free_ids(for_author_id: int | None) -> dict[str, str | None]:
        """The incoming ids this row may adopt without colliding.

        Every id column is UNIQUE, and sources disagree: OpenAlex and Semantic
        Scholar routinely split or merge the same person, so a record can arrive
        carrying an id that already belongs to a different row. Writing it
        anyway fails the whole ingest, so an id already spoken for is simply
        left out — the existing owner keeps it.
        """
        out: dict[str, str | None] = {}
        for col in ID_COLUMNS:
            val = author.get(col)
            if not val:
                out[col] = None
                continue
            owner = conn.execute(
                f"SELECT id FROM authors WHERE {col} = ?", (val,)
            ).fetchone()
            out[col] = val if owner is None or owner["id"] == for_author_id else None
        return out

    for col in ID_COLUMNS:
        val = author.get(col)
        if val:
            row = conn.execute(f"SELECT id FROM authors WHERE {col} = ?", (val,)).fetchone()
            if row:
                take = free_ids(row["id"])
                conn.execute(
                    "UPDATE authors SET name = COALESCE(?, name), orcid = COALESCE(?, orcid), "
                    "openalex_id = COALESCE(?, openalex_id), s2_author_id = COALESCE(?, s2_author_id), "
                    "updated_at = datetime('now') WHERE id = ?",
                    (name, take["orcid"], take["openalex_id"], take["s2_author_id"], row["id"]),
                )
                return row["id"]

    # Fall back to name matching, but only for authors that carry no external
    # id at all. Two distinct people can share a name, so this stays last.
    norm = ids.norm_name(name)
    row = conn.execute(
        "SELECT id FROM authors WHERE normalized_name = ? AND orcid IS NULL "
        "AND openalex_id IS NULL AND s2_author_id IS NULL",
        (norm,),
    ).fetchone()
    if row:
        return row["id"]

    take = free_ids(None)
    cur = conn.execute(
        "INSERT INTO authors(name, normalized_name, orcid, openalex_id, s2_author_id) "
        "VALUES(?,?,?,?,?)",
        (name, norm, take["orcid"], take["openalex_id"], take["s2_author_id"]),
    )
    return cur.lastrowid


def _store_authors(conn: sqlite3.Connection, paper_id: int, authors: list[dict[str, Any]]) -> None:
    seen: set[int] = set()
    for author in authors or []:
        author_id = _upsert_author(conn, author)
        if author_id is None or author_id in seen:
            continue  # a paper listing the same person twice would break the PK
        seen.add(author_id)

        conn.execute(
            "INSERT INTO paper_authors(paper_id, author_id, position, is_corresponding) "
            "VALUES(?,?,?,?) ON CONFLICT(paper_id, author_id) DO UPDATE SET "
            "position = excluded.position, is_corresponding = excluded.is_corresponding",
            (paper_id, author_id, author.get("position", 0), int(bool(author.get("is_corresponding")))),
        )

        primary_country = None
        primary_name = None
        for inst in author.get("institutions") or []:
            inst_id = _upsert_institution(conn, inst)
            if inst_id is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO authorship_institutions(paper_id, author_id, institution_id) "
                "VALUES(?,?,?)",
                (paper_id, author_id, inst_id),
            )
            if primary_name is None:
                primary_name, primary_country = inst.get("name"), inst.get("country_code")

        if primary_name:
            conn.execute(
                "UPDATE authors SET affiliation = COALESCE(affiliation, ?), "
                "country = COALESCE(country, ?) WHERE id = ?",
                (primary_name, primary_country, author_id),
            )


def _store_topics(conn: sqlite3.Connection, paper_id: int, topics: list[dict[str, Any]]) -> None:
    for topic in topics or []:
        name, oa = topic.get("name"), topic.get("openalex_id")
        if not name:
            continue
        row = None
        if oa:
            row = conn.execute("SELECT id FROM topics WHERE openalex_id = ?", (oa,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM topics WHERE openalex_id IS NULL AND name = ?", (name,)
            ).fetchone()
        if row:
            topic_id = row["id"]
        else:
            topic_id = conn.execute(
                "INSERT INTO topics(openalex_id, name, subfield, field, domain) VALUES(?,?,?,?,?)",
                (oa, name, topic.get("subfield"), topic.get("field"), topic.get("domain")),
            ).lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO paper_topics(paper_id, topic_id, score) VALUES(?,?,?)",
            (paper_id, topic_id, topic.get("score")),
        )


# ---------------------------------------------------------------------------
# papers
# ---------------------------------------------------------------------------
PAPER_COLUMNS = (
    "title", "year", "month", "venue", "volume", "pages", "publisher", "abstract",
    "doi", "arxiv_id", "s2_id", "openalex_id", "pmid", "url", "citation_count",
    "reference_count", "influential_citation_count", "is_oa", "oa_status",
    "oa_pdf_url", "license",
)


def upsert_paper(conn: sqlite3.Connection, rec: dict[str, Any]) -> tuple[int, bool]:
    """Insert or update a paper. Returns (paper_id, created)."""
    rec = dict(rec)
    rec.update(ids.identity_keys(rec))
    if not rec.get("title"):
        raise ValueError("paper record has no title")

    existing = find_paper_id(conn, rec)
    values = {k: rec.get(k) for k in PAPER_COLUMNS}
    values["is_oa"] = int(bool(values.get("is_oa")))
    sources = json.dumps(sorted(set(rec.get("sources") or [])))

    if existing is None:
        cols = ", ".join(PAPER_COLUMNS) + ", sources, synced_at"
        marks = ", ".join("?" * len(PAPER_COLUMNS)) + ", ?, datetime('now')"
        cur = conn.execute(
            f"INSERT INTO papers({cols}) VALUES({marks})",
            [values[k] for k in PAPER_COLUMNS] + [sources],
        )
        paper_id = cur.lastrowid
        created = True
    else:
        paper_id = existing
        # COALESCE keeps whatever is already stored when the new value is null,
        # so a thin source can never blank a field a richer one filled.
        assigns = ", ".join(f"{k} = COALESCE(?, {k})" for k in PAPER_COLUMNS)
        conn.execute(
            f"UPDATE papers SET {assigns}, sources = ?, synced_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            [values[k] for k in PAPER_COLUMNS] + [sources, paper_id],
        )
        created = False

    _store_authors(conn, paper_id, rec.get("authors") or [])
    _store_topics(conn, paper_id, rec.get("topics") or [])
    return paper_id, created


# ---------------------------------------------------------------------------
# pending links
# ---------------------------------------------------------------------------
# What `pending_links` actually stores. It has no pmid column, so a query
# built from the full identity set fails against it. Exported because ingest
# queries that table too.
PENDING_COLUMNS = ("doi", "arxiv_id", "s2_id", "openalex_id")
_PENDING_COLUMNS = PENDING_COLUMNS


def store_pending(
    conn: sqlite3.Connection, paper_id: int, direction: str, records: Iterable[dict[str, Any]]
) -> int:
    """Record the identifiers of referenced / citing papers. Creates no papers."""
    stored = 0
    for rec in records:
        keys = ids.identity_keys(rec)
        if not any(keys.values()):
            continue  # nothing to match on later; useless

        # The unique indexes key on (from_paper_id, direction, doi) and, when
        # there is no doi, on s2_id. Matching on all four columns instead is
        # too narrow: one reference list can carry the same DOI twice with
        # different secondary ids, which passes this check and then trips the
        # index. So dedupe on whichever column actually constrains the row.
        if keys["doi"]:
            narrow, value = "doi = ?", keys["doi"]
        elif keys["s2_id"]:
            narrow, value = "doi IS NULL AND s2_id = ?", keys["s2_id"]
        else:
            narrow, value = None, None

        if narrow:
            dup = conn.execute(
                f"SELECT id FROM pending_links WHERE from_paper_id = ? AND direction = ? "
                f"AND {narrow}",
                (paper_id, direction, value),
            ).fetchone()
        else:
            where = " AND ".join(
                f"{c} IS ?" if keys[c] is None else f"{c} = ?" for c in _PENDING_COLUMNS
            )
            dup = conn.execute(
                f"SELECT id FROM pending_links WHERE from_paper_id = ? AND direction = ? "
                f"AND {where}",
                [paper_id, direction] + [keys[c] for c in _PENDING_COLUMNS],
            ).fetchone()
        if dup:
            continue

        # Backstop: the checks above cover the indexes, but a duplicate is
        # never worth failing an import over.
        conn.execute(
            "INSERT INTO pending_links(from_paper_id, direction, doi, arxiv_id, s2_id, "
            "openalex_id, title, year, authors_blob, citation_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                paper_id, direction, keys["doi"], keys["arxiv_id"], keys["s2_id"],
                keys["openalex_id"], rec.get("title"), rec.get("year"),
                rec.get("authors_blob"), rec.get("citation_count"),
            ),
        )
        stored += 1
    return stored


def _create_link(conn: sqlite3.Connection, pending: sqlite3.Row, other_id: int) -> bool:
    """Turn one resolved pending row into a link. Returns True if a row was added."""
    from_id = pending["from_paper_id"]
    if from_id == other_id:
        return False

    # 'reference' means from_paper cites other; 'citation' means other cites from_paper.
    if pending["direction"] == "reference":
        src, dst = from_id, other_id
    else:
        src, dst = other_id, from_id

    # Every automatic link is a plain citation. Typing them is a judgement the
    # user makes deliberately, not something inferred from a third party.
    link_type = "cites"

    # Never overwrite a link you asserted by hand.
    existing = conn.execute(
        "SELECT id, origin FROM links WHERE src_paper_id = ? AND dst_paper_id = ? AND type = ?",
        (src, dst, link_type),
    ).fetchone()
    if existing:
        return False

    conn.execute(
        "INSERT INTO links(src_paper_id, dst_paper_id, type, context, intent, "
        "is_influential, origin, confirmed) VALUES(?,?,?,?,?,?,'auto',0)",
        (src, dst, link_type, pending["context"], pending["intent"], pending["is_influential"]),
    )
    return True


def resolve_links(conn: sqlite3.Connection, paper_id: int | None = None) -> int:
    """Match unresolved pending rows against papers in the library.

    Called after every add. With `paper_id` it does the two-sided check for that
    paper only: rows *about* it recorded by earlier papers, and its own rows
    pointing at papers already present. With no argument it sweeps everything,
    which is what the maintenance endpoint uses.
    """
    created = 0
    rows: list[sqlite3.Row] = []

    if paper_id is None:
        rows = conn.execute("SELECT * FROM pending_links WHERE resolved_paper_id IS NULL").fetchall()
        targets = {r["id"]: None for r in rows}
    else:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if paper is None:
            return 0
        # rows recorded by other papers that point at this one
        clauses, args = [], []
        for col in _PENDING_COLUMNS:
            if paper[col]:
                clauses.append(f"{col} = ?")
                args.append(paper[col])
        if clauses:
            rows.extend(
                conn.execute(
                    f"SELECT * FROM pending_links WHERE resolved_paper_id IS NULL "
                    f"AND from_paper_id != ? AND ({' OR '.join(clauses)})",
                    [paper_id] + args,
                ).fetchall()
            )
            for pending in rows:
                if _create_link(conn, pending, paper_id):
                    created += 1
                conn.execute(
                    "UPDATE pending_links SET resolved_paper_id = ?, resolved_at = datetime('now') "
                    "WHERE id = ?",
                    (paper_id, pending["id"]),
                )
        # this paper's own rows, pointing at papers already in the library
        rows = conn.execute(
            "SELECT * FROM pending_links WHERE resolved_paper_id IS NULL AND from_paper_id = ?",
            (paper_id,),
        ).fetchall()

    for pending in rows:
        other = find_paper_id(conn, dict(pending))
        if other is None:
            continue
        if _create_link(conn, pending, other):
            created += 1
        conn.execute(
            "UPDATE pending_links SET resolved_paper_id = ?, resolved_at = datetime('now') "
            "WHERE id = ?",
            (other, pending["id"]),
        )

    return created
