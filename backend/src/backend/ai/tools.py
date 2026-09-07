"""The assistant's tools.

Schemas are written by hand rather than derived from signatures. The
description is what decides whether the model reaches for the right tool, and
it needs prose aimed at the model — "the user's own library, not the internet"
says something a type annotation cannot.

Two rules hold throughout:

  * A tool never raises. Failures come back as ``{"error": ...}`` so the model
    can recover, rather than killing the turn with a traceback the user cannot
    act on.
  * Every result is capped. Handing back a whole library would blow the context
    window on a moderately sized collection, so each tool returns a bounded
    page plus a note saying what was left out.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any, Awaitable, Callable

import httpx

from .. import db, store
from ..sources import openalex, search as websearch, wikipedia
from ..sources.http import SourceError
from . import skills as skills_mod

log = logging.getLogger(__name__)

ROW_CAP = 25
TEXT_CAP = 1200


def _cap(rows: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    total = len(rows) if total is None else total
    shown = rows[:ROW_CAP]
    out: dict[str, Any] = {"results": shown, "shown": len(shown), "total": total}
    if total > len(shown):
        out["note"] = f"Showing {len(shown)} of {total}. Narrow the query to see others."
    return out


def _clip(text: str | None, limit: int = TEXT_CAP) -> str | None:
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + " …"


# ---------------------------------------------------------------------------
# reading the library
# ---------------------------------------------------------------------------
def search_library(conn: sqlite3.Connection, query: str, limit: int = 10) -> dict[str, Any]:
    select = (
        "SELECT p.id, p.title, p.year, p.venue, p.doi, p.status, p.citation_count, "
        "  (SELECT group_concat(a.name, ', ') FROM paper_authors pa "
        "   JOIN authors a ON a.id = pa.author_id WHERE pa.paper_id = p.id) AS authors "
        "FROM papers p "
    )
    rows: list[sqlite3.Row] = []
    try:
        rows = conn.execute(
            select
            + "WHERE p.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?) "
            "ORDER BY p.year DESC LIMIT ?",
            (query, min(limit, ROW_CAP)),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []  # FTS rejects some punctuation; fall through to LIKE
    if not rows:
        rows = conn.execute(
            select + "WHERE p.title LIKE ? ORDER BY p.year DESC LIMIT ?",
            (f"%{query}%", min(limit, ROW_CAP)),
        ).fetchall()
    return _cap([dict(r) for r in rows])


def list_papers(
    conn: sqlite3.Connection, status: str | None = None, shelf: str | None = None
) -> dict[str, Any]:
    sql = ["SELECT p.id, p.title, p.year, p.venue, p.status, p.citation_count FROM papers p"]
    where, args = [], []
    if shelf:
        sql.append(
            "JOIN paper_shelves ps ON ps.paper_id = p.id "
            "JOIN shelves s ON s.id = ps.shelf_id AND s.name = ?"
        )
        args.append(shelf)
    if status:
        where.append("p.status = ?")
        args.append(status)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY p.year DESC")
    rows = conn.execute("\n".join(sql), args).fetchall()
    return _cap([dict(r) for r in rows])


def get_paper(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        return {"error": f"No paper with id {paper_id} in the library."}
    paper = dict(row)
    paper["abstract"] = _clip(paper.get("abstract"))
    paper["authors"] = [
        dict(a)
        for a in conn.execute(
            "SELECT a.id, a.name, a.affiliation, a.country, pa.position "
            "FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.paper_id = ? ORDER BY pa.position",
            (paper_id,),
        )
    ]
    paper["topics"] = [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM paper_topics pt JOIN topics t ON t.id = pt.topic_id "
            "WHERE pt.paper_id = ?",
            (paper_id,),
        )
    ]
    note = conn.execute("SELECT body FROM notes WHERE paper_id = ?", (paper_id,)).fetchone()
    paper["note"] = _clip(note["body"]) if note else None
    for drop in ("sources", "oa_pdf_url", "url", "owner_id"):
        paper.pop(drop, None)
    return paper


def list_authors(conn: sqlite3.Connection, query: str | None = None) -> dict[str, Any]:
    sql = (
        "SELECT a.id, a.name, a.affiliation, a.country, a.orcid, "
        "  COUNT(pa.paper_id) AS paper_count, "
        "  (SELECT url FROM author_links al WHERE al.author_id = a.id "
        "     AND al.kind='homepage' AND al.status='found') AS homepage "
        "FROM authors a JOIN paper_authors pa ON pa.author_id = a.id "
    )
    args: list[Any] = []
    if query:
        sql += "WHERE a.name LIKE ? "
        args.append(f"%{query}%")
    sql += "GROUP BY a.id ORDER BY paper_count DESC, a.name"
    return _cap([dict(r) for r in conn.execute(sql, args)])


def get_author(conn: sqlite3.Connection, author_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        return {"error": f"No author with id {author_id}."}
    author = dict(row)
    author["papers"] = [
        dict(p)
        for p in conn.execute(
            "SELECT p.id, p.title, p.year FROM paper_authors pa "
            "JOIN papers p ON p.id = pa.paper_id WHERE pa.author_id = ? ORDER BY p.year",
            (author_id,),
        )
    ]
    profile = conn.execute(
        "SELECT status, title, url, description FROM author_profiles WHERE author_id = ?",
        (author_id,),
    ).fetchone()
    author["wikipedia"] = dict(profile) if profile else None
    author["links"] = {
        r["kind"]: {"url": r["url"], "status": r["status"], "verified": bool(r["verified"])}
        for r in conn.execute(
            "SELECT kind, url, status, verified FROM author_links WHERE author_id = ?",
            (author_id,),
        )
    }
    return author


def get_paper_links(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    """Citations both ways, restricted to papers actually in the library."""
    rows = conn.execute(
        "SELECT l.id, l.type, l.note, l.src_paper_id, l.dst_paper_id, "
        "  ps.title AS src_title, ps.year AS src_year, "
        "  pd.title AS dst_title, pd.year AS dst_year "
        "FROM links l JOIN papers ps ON ps.id = l.src_paper_id "
        "JOIN papers pd ON pd.id = l.dst_paper_id "
        "WHERE l.src_paper_id = ? OR l.dst_paper_id = ?",
        (paper_id, paper_id),
    ).fetchall()
    pending = conn.execute(
        "SELECT COUNT(*) c FROM pending_links WHERE from_paper_id = ? "
        "AND resolved_paper_id IS NULL",
        (paper_id,),
    ).fetchone()["c"]
    out = _cap([dict(r) for r in rows])
    out["unresolved"] = pending
    out["explanation"] = (
        f"{pending} referenced papers are recorded by identifier but are not in "
        "the library, so they have no link yet."
    )
    return out


# ---------------------------------------------------------------------------
# writing: annotations only. Adding a paper stays the user's decision.
# ---------------------------------------------------------------------------
def set_author_homepage(
    conn: sqlite3.Connection, author_id: int, url: str, verified: bool = False
) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM authors WHERE id = ?", (author_id,)).fetchone() is None:
        return {"error": f"No author with id {author_id}."}
    conn.execute(
        "INSERT INTO author_links(author_id, kind, status, url, verified, found_by) "
        "VALUES(?, 'homepage', 'found', ?, ?, 'assistant') "
        "ON CONFLICT(author_id, kind) DO UPDATE SET url=excluded.url, status='found', "
        "  verified=excluded.verified, found_by='assistant', fetched_at=datetime('now')",
        (author_id, url, 1 if verified else 0),
    )
    return {"ok": True, "author_id": author_id, "url": url, "verified": verified}


def set_author_bio(
    conn: sqlite3.Connection, author_id: int, bio: str, source: str = "assistant"
) -> dict[str, Any]:
    """Write an author's biography.

    Wikipedia is one source for a bio, not its definition. Most researchers
    have no Wikipedia page, and a short factual paragraph assembled from their
    own pages is more useful than an empty field.
    """
    if conn.execute("SELECT 1 FROM authors WHERE id = ?", (author_id,)).fetchone() is None:
        return {"error": f"No author with id {author_id}."}
    text = (bio or "").strip()
    if len(text) < 20:
        return {"error": "That bio is too short to be useful."}

    conn.execute(
        "INSERT INTO author_profiles(author_id, status, bio, bio_source, bio_updated_at) "
        "VALUES(?, 'none', ?, ?, datetime('now')) "
        "ON CONFLICT(author_id) DO UPDATE SET bio = excluded.bio, "
        "  bio_source = excluded.bio_source, bio_updated_at = excluded.bio_updated_at",
        (author_id, text, source),
    )
    return {"ok": True, "author_id": author_id, "chars": len(text)}


def add_note(
    conn: sqlite3.Connection, paper_id: int, text: str, append: bool = True
) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
        return {"error": f"No paper with id {paper_id}."}
    existing = conn.execute(
        "SELECT id, body FROM notes WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if existing:
        body = f"{existing['body']}\n\n{text}" if append and existing["body"] else text
        conn.execute(
            "UPDATE notes SET body = ?, updated_at = datetime('now') WHERE id = ?",
            (body, existing["id"]),
        )
    else:
        conn.execute("INSERT INTO notes(paper_id, body) VALUES(?,?)", (paper_id, text))
    return {"ok": True, "paper_id": paper_id}


def set_paper_status(conn: sqlite3.Connection, paper_id: int, status: str) -> dict[str, Any]:
    allowed = {"unread", "queued", "skimmed", "read", "archived"}
    if status not in allowed:
        return {"error": f"status must be one of {sorted(allowed)}"}
    cur = conn.execute(
        "UPDATE papers SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, paper_id),
    )
    if cur.rowcount == 0:
        return {"error": f"No paper with id {paper_id}."}
    return {"ok": True, "paper_id": paper_id, "status": status}


def create_link(
    conn: sqlite3.Connection,
    src_paper_id: int,
    dst_paper_id: int,
    type: str,
    note: str | None = None,
) -> dict[str, Any]:
    if src_paper_id == dst_paper_id:
        return {"error": "A paper cannot link to itself."}
    known = {r["type"] for r in conn.execute("SELECT type FROM link_types")}
    if type not in known:
        return {"error": f"type must be one of {sorted(known)}"}
    try:
        cur = conn.execute(
            "INSERT INTO links(src_paper_id, dst_paper_id, type, note, origin, confirmed) "
            "VALUES(?,?,?,?, 'assistant', 0)",
            (src_paper_id, dst_paper_id, type, note),
        )
    except sqlite3.IntegrityError as exc:
        return {"error": f"Could not create the link: {exc}"}
    return {"ok": True, "link_id": cur.lastrowid}


# ---------------------------------------------------------------------------
# outside the library
# ---------------------------------------------------------------------------
async def web_search(
    conn: sqlite3.Connection, client: httpx.AsyncClient, query: str
) -> dict[str, Any]:
    settings = db.get_settings(conn)
    try:
        results = await websearch.search(
            client,
            query,
            settings.get("search_provider", "brave"),
            settings.get("search_api_key", ""),
        )
    except SourceError as exc:
        return {"error": str(exc)}
    return _cap([{**r, "snippet": _clip(r.get("snippet"), 300)} for r in results])


_HREF_RE = re.compile(r"""<a\b[^>]*?href=["']([^"'#][^"']*)["'][^>]*>(.*?)</a>""", re.S | re.I)
_IMG_RE = re.compile(r"""<img\b[^>]*?src=["']([^"']+)["']""", re.I)


def _absolute(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)


async def fetch_url(
    conn: sqlite3.Connection, client: httpx.AsyncClient, url: str, links: bool = True
) -> dict[str, Any]:
    """Read a page: its text, and the links and images it contains.

    Text alone is not enough. Finding a PDF or a portrait means finding a URL,
    and stripping the markup would throw away every href and src on the page —
    leaving the assistant able to read that a download exists but not where it
    points.
    """
    try:
        resp = await client.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"error": f"Could not fetch {url}: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"{url} returned HTTP {resp.status_code}"}

    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type or resp.content[:4] == b"%PDF":
        return {
            "url": str(resp.url),
            "content_type": "application/pdf",
            "note": "This is a PDF, not a page. Use attach_pdf to store it against a paper.",
        }

    raw = resp.text[:800_000]
    final = str(resp.url)

    collected: list[dict[str, str]] = []
    images: list[str] = []
    if links:
        seen: set[str] = set()
        for href, label in _HREF_RE.findall(raw):
            target = _absolute(final, href.strip())
            if not target.startswith("http") or target in seen:
                continue
            seen.add(target)
            text = re.sub(r"<[^>]+>", " ", label)
            text = re.sub(r"\s+", " ", text).strip()
            collected.append({"url": target, "text": text[:80]})
            if len(collected) >= 60:
                break
        for src in _IMG_RE.findall(raw):
            target = _absolute(final, src.strip())
            if target.startswith("http") and target not in images:
                images.append(target)
            if len(images) >= 15:
                break

    text = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    out: dict[str, Any] = {
        "url": final,
        "status": resp.status_code,
        "text": _clip(text, 4000),
    }
    if links:
        out["links"] = collected
        out["images"] = images
        # A page usually has more links than are worth carrying; say so rather
        # than letting the assistant assume it saw them all.
        pdfs = [l for l in collected if ".pdf" in l["url"].lower()]
        if pdfs:
            out["pdf_links"] = pdfs[:10]
    return out


async def lookup_identifier(
    conn: sqlite3.Connection, client: httpx.AsyncClient, identifier: str
) -> dict[str, Any]:
    """Look a paper up in the metadata sources without adding it."""
    from .. import ingest

    try:
        bundle = await ingest.fetch_bundle(conn, identifier)
    except (ValueError, LookupError) as exc:
        return {"error": str(exc)}
    m = bundle.merged
    return {
        "title": m.get("title"),
        "year": m.get("year"),
        "venue": m.get("venue"),
        "doi": m.get("doi"),
        "authors": [a.get("name") for a in (m.get("authors") or [])],
        "abstract": _clip(m.get("abstract"), 700),
        "citation_count": m.get("citation_count"),
        "in_library": store.find_paper_id(conn, m) is not None,
        "references": len(bundle.references),
    }


async def set_author_photo(
    conn: sqlite3.Connection, client: httpx.AsyncClient, author_id: int, url: str
) -> dict[str, Any]:
    """Fetch an image URL and store it as this author's portrait."""
    from .. import photos

    if conn.execute("SELECT 1 FROM authors WHERE id = ?", (author_id,)).fetchone() is None:
        return {"error": f"No author with id {author_id}."}
    return await photos.save_from_url(conn, client, author_id, url)


def pdf_sources(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    """Where the full text of this paper can legitimately be got."""
    row = conn.execute(
        "SELECT id, title, doi, arxiv_id, pmid, is_oa, oa_status, oa_pdf_url, pdf_path "
        "FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        return {"error": f"No paper with id {paper_id}."}

    routes = []
    if row["arxiv_id"]:
        routes.append({"kind": "arxiv", "url": f"https://arxiv.org/pdf/{row['arxiv_id']}"})
    if row["oa_pdf_url"]:
        routes.append({"kind": "open_access", "url": row["oa_pdf_url"]})
    if row["doi"]:
        routes.append({"kind": "publisher", "url": f"https://doi.org/{row['doi']}"})

    return {
        "paper_id": paper_id,
        "title": row["title"],
        "has_pdf": bool(row["pdf_path"]),
        "is_open_access": bool(row["is_oa"]),
        "oa_status": row["oa_status"],
        "known_routes": routes,
        "note": (
            "Already stored; there is nothing to fetch."
            if row["pdf_path"]
            else "Open access, so a copy should be downloadable."
            if row["is_oa"]
            else "Not open access. A legitimate copy may still exist in an institutional "
                 "repository or on an author's own page; there may equally be none, which "
                 "is a normal outcome to report."
        ),
    }


async def attach_pdf(
    conn: sqlite3.Connection, client: httpx.AsyncClient, paper_id: int, url: str
) -> dict[str, Any]:
    """Download a PDF and store it against a paper.

    Only fetches the URL it is given. The file must actually be a PDF, checked
    from its leading bytes rather than the server's content type.
    """
    from .. import db as _db

    row = conn.execute("SELECT pdf_path FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        return {"error": f"No paper with id {paper_id}."}
    if row["pdf_path"]:
        return {
            "error": "This paper already has a PDF. Delete it first if you mean to replace it.",
        }

    try:
        resp = await client.get(url, timeout=120.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"error": f"Could not fetch {url}: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"{url} returned HTTP {resp.status_code}."}

    body = resp.content
    if not body.startswith(b"%PDF"):
        return {
            "error": (
                "That URL did not return a PDF. Landing pages often look like a download "
                "link but serve HTML; find the direct file link instead."
            ),
        }
    if len(body) > 200 * 1024 * 1024:
        return {"error": "That PDF is larger than 200 MB."}

    rel = f"pdfs/{paper_id}.pdf"
    (_db.library_root() / rel).write_bytes(body)
    conn.execute("UPDATE papers SET pdf_path = ? WHERE id = ?", (rel, paper_id))
    return {"ok": True, "paper_id": paper_id, "bytes": len(body), "source": str(resp.url)}


def read_pdf(
    conn: sqlite3.Connection, paper_id: int, page: int | None = None
) -> dict[str, Any]:
    """The text of a stored PDF, a page at a time."""
    from .. import pdftext

    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return {"error": "No PDF is stored for this paper."}

    pages = pdftext.extract_text(path)
    if page is not None:
        pages = [p for p in pages if p["page"] == page]
        if not pages:
            return {"error": f"That PDF has no page {page}."}
    # A whole paper would swamp the context, so hand back one page at a time
    # and say how many there are.
    total = len(pdftext.extract_text(path)) if page is None else None
    if page is None and len(pages) > 1:
        return {
            "page_count": len(pages),
            "page": 1,
            "text": _clip(pages[0]["text"], 6000),
            "note": f"Page 1 of {len(pages)}. Ask for a specific page, or use search_pdf.",
        }
    return {
        "page": pages[0]["page"],
        "page_count": total or 1,
        "text": _clip(pages[0]["text"], 6000),
    }


def search_pdf(conn: sqlite3.Connection, paper_id: int, query: str) -> dict[str, Any]:
    """Find a phrase inside a stored PDF and see it in context."""
    from .. import pdftext

    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return {"error": "No PDF is stored for this paper."}
    hits = pdftext.search_text(path, query)
    return _cap(hits) if hits else {"results": [], "shown": 0, "total": 0,
                                    "note": f"'{query}' does not appear in this PDF."}


def pdf_outline(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    """The paper's section headings with their pages."""
    from .. import pdftext

    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return {"error": "No PDF is stored for this paper."}
    entries = pdftext.outline(path)
    if not entries:
        return {"outline": [], "note": "This PDF has no embedded table of contents."}
    return {"outline": entries[:80], "total": len(entries)}


def show_in_pdf(
    conn: sqlite3.Connection,
    paper_id: int,
    page: int | None = None,
    quote: str | None = None,
) -> dict[str, Any]:
    """Bring a page or a passage into view in the reader.

    Points the user at what you are talking about instead of describing where
    to look. Nothing is stored: this only moves the view.
    """
    from .. import pdftext

    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return {"error": "No PDF is stored for this paper, so there is nothing to show."}

    if quote:
        found = pdftext.find_quote(path, quote, page)
        if found is None:
            return {
                "error": (
                    "Could not find that passage. Quote it as it appears, or use "
                    "search_pdf to check the wording."
                ),
            }
        return {
            "ok": True, "paper_id": paper_id, "page": found["page"],
            "rects": found["rects"], "quote": quote,
        }

    if page is None:
        return {"error": "Give a page number or a quote to show."}
    pages = pdftext.extract_text(path)
    if not any(p["page"] == page for p in pages):
        return {"error": f"That PDF has no page {page}."}
    return {"ok": True, "paper_id": paper_id, "page": page, "rects": []}


def add_highlight(
    conn: sqlite3.Connection,
    paper_id: int,
    quote: str,
    comment: str | None = None,
    page: int | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Highlight a passage by quoting it, optionally with a note attached.

    The quote is located in the file so the highlight lands on the right words:
    a passage cannot be marked from text alone without knowing where it is
    drawn on the page.
    """
    import json as _json

    from .. import pdftext

    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return {"error": "No PDF is stored for this paper, so there is nothing to mark."}

    found = pdftext.find_quote(path, quote, page)
    if found is None:
        return {
            "error": (
                f"Could not find that passage in the PDF. Quote it exactly as it appears, "
                "or use search_pdf first to check the wording."
            ),
        }

    cur = conn.execute(
        "INSERT INTO highlights(paper_id, page, rects, quoted, comment, color, "
        "  page_width, page_height, source) VALUES(?,?,?,?,?,?,?,?, 'assistant')",
        (
            paper_id, found["page"], _json.dumps(found["rects"]), quote.strip(),
            comment, color or "#a78bfa", found["width"], found["height"],
        ),
    )
    return {
        "ok": True,
        "highlight_id": cur.lastrowid,
        "page": found["page"],
        "lines": len(found["rects"]),
        # The geometry, so the reader can scroll to the new mark. Named as the
        # rectangles they are: this key previously held a count, which the UI
        # then tried to iterate.
        "rects": found["rects"],
    }


def list_highlights(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    """Passages already marked in this paper, with any notes on them."""
    rows = conn.execute(
        "SELECT id, page, quoted, comment, color, source, created_at "
        "FROM highlights WHERE paper_id = ? ORDER BY page, id",
        (paper_id,),
    ).fetchall()
    return _cap([dict(r) for r in rows])


async def find_author_works(
    conn: sqlite3.Connection, client: httpx.AsyncClient, author_id: int
) -> dict[str, Any]:
    """Everything OpenAlex attributes to this author, not just what is held."""
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        return {"error": f"No author with id {author_id}."}
    if not row["openalex_id"]:
        return {"error": "This author has no OpenAlex id, so their output cannot be listed."}

    email = db.get_setting(conn, "contact_email")
    try:
        works = await openalex.fetch_author_works(client, row["openalex_id"], email, 60)
    except SourceError as exc:
        return {"error": str(exc)}
    for w in works:
        w["in_library"] = store.find_paper_id(conn, w) is not None
    return _cap(
        [
            {k: w.get(k) for k in ("title", "year", "doi", "citation_count", "in_library", "venue")}
            for w in works
        ]
    )


async def find_wikipedia(
    conn: sqlite3.Connection, client: httpx.AsyncClient, author_id: int
) -> dict[str, Any]:
    """Resolve an author's Wikipedia page through Wikidata.

    Exact rather than guessed: the page's Wikidata item must be a human, which
    is what stops a namesake stadium or novel being taken for a biography.
    """
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        return {"error": f"No author with id {author_id}."}
    try:
        found = await wikipedia.fetch_profile(client, row["name"], row["affiliation"])
    except SourceError as exc:
        return {"error": str(exc)}

    conn.execute(
        "INSERT INTO author_profiles(author_id, status, title, url, description, extract, "
        "  thumbnail_url, image_url, detail, fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?, datetime('now')) "
        "ON CONFLICT(author_id) DO UPDATE SET status=excluded.status, title=excluded.title, "
        "  url=excluded.url, description=excluded.description, extract=excluded.extract, "
        "  thumbnail_url=excluded.thumbnail_url, image_url=excluded.image_url, "
        "  detail=excluded.detail, fetched_at=excluded.fetched_at",
        (
            author_id,
            found["status"],
            found.get("title"),
            found.get("url"),
            found.get("description"),
            found.get("extract"),
            found.get("thumbnail_url"),
            found.get("image_url"),
            found.get("detail"),
        ),
    )
    return {**found, "extract": _clip(found.get("extract"), 800)}


# ---------------------------------------------------------------------------
# skills and direct queries
# ---------------------------------------------------------------------------
SQL_ROW_CAP = 100
# Only these may begin a statement. The read-only connection is the real
# guarantee; this rejects the obvious cases with a clearer message first.
_SQL_ALLOWED = re.compile(r"^\s*(select|with)\b", re.I)


def read_skill(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Load one skill's full instructions."""
    skills_mod.reload()   # an edited skill takes effect without a restart
    skill = skills_mod.read(name, conn)
    if skill is None:
        return {
            "error": f"There is no skill called '{name}'.",
            "available": [s.name for s in skills_mod.index()],
        }
    return {"name": skill.name, "description": skill.description, "instructions": skill.body}


def run_sql(conn: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Run one read-only SELECT against the library.

    Opened through a second, read-only connection rather than the request's
    own. Raw SQL would otherwise bypass every check the dedicated write tools
    carry — including the rule that only the user adds papers — so the database
    itself refuses the write rather than trusting a pattern match.
    """
    if not _SQL_ALLOWED.match(query or ""):
        return {
            "error": "Only SELECT (or WITH ... SELECT) queries are allowed. To change "
                     "something, use the tool for it: add_note, create_link, "
                     "set_paper_status, set_author_homepage.",
        }
    if ";" in query.rstrip().rstrip(";"):
        return {"error": "Send a single statement, without extra semicolons."}

    try:
        ro = sqlite3.connect(f"file:{db.db_path()}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        return {"error": f"Could not open the database read-only: {exc}"}

    try:
        ro.row_factory = sqlite3.Row
        # Stop a runaway query rather than hanging the turn.
        deadline = time.monotonic() + 10.0
        ro.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        cur = ro.execute(query)
        rows = cur.fetchmany(SQL_ROW_CAP + 1)
        columns = [d[0] for d in (cur.description or [])]
    except sqlite3.Error as exc:
        return {"error": f"SQL error: {exc}"}
    finally:
        ro.close()

    truncated = len(rows) > SQL_ROW_CAP
    out: dict[str, Any] = {
        "columns": columns,
        "rows": [dict(r) for r in rows[:SQL_ROW_CAP]],
        "row_count": min(len(rows), SQL_ROW_CAP),
    }
    if truncated:
        out["note"] = f"More than {SQL_ROW_CAP} rows; add a LIMIT or aggregate instead."
    return out


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
SYNC_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_library": search_library,
    "list_papers": list_papers,
    "get_paper": get_paper,
    "list_authors": list_authors,
    "get_author": get_author,
    "get_paper_links": get_paper_links,
    "pdf_sources": pdf_sources,
    "read_pdf": read_pdf,
    "search_pdf": search_pdf,
    "add_highlight": add_highlight,
    "show_in_pdf": show_in_pdf,
    "pdf_outline": pdf_outline,
    "list_highlights": list_highlights,
    "set_author_homepage": set_author_homepage,
    "set_author_bio": set_author_bio,
    "add_note": add_note,
    "set_paper_status": set_paper_status,
    "create_link": create_link,
    "read_skill": read_skill,
    "run_sql": run_sql,
}

ASYNC_TOOLS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "lookup_identifier": lookup_identifier,
    "find_author_works": find_author_works,
    "find_wikipedia": find_wikipedia,
    "set_author_photo": set_author_photo,
    "attach_pdf": attach_pdf,
}

# Tools that change the database. Surfaced in the UI so writes are visible.
WRITE_TOOLS = {
    "set_author_homepage", "set_author_bio", "set_author_photo", "attach_pdf",
    "add_note", "add_highlight", "set_paper_status", "create_link",
}


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema(
        "search_library",
        "Full-text search over the papers the user has already added. This searches their "
        "own library, not the internet. Use it first whenever they refer to a paper "
        "without giving an id.",
        {"query": {**_STR, "description": "Words from the title, abstract or notes."},
         "limit": _INT},
        ["query"],
    ),
    _schema(
        "list_papers",
        "List papers in the library, optionally filtered by reading status or shelf. Use "
        "for questions like 'what am I meant to be reading' or 'what is on my supplement "
        "shelf'.",
        {"status": {**_STR, "description": "unread | queued | skimmed | read | archived"},
         "shelf": _STR},
        [],
    ),
    _schema(
        "get_paper",
        "Everything the library holds on one paper: metadata, authors with their "
        "affiliations, topics, and the user's own note.",
        {"paper_id": _INT},
        ["paper_id"],
    ),
    _schema(
        "list_authors",
        "Authors of papers in the library, with how many papers each appears on and their "
        "homepage if one is known. Use this to turn a name into an id.",
        {"query": {**_STR, "description": "Optional substring of the name."}},
        [],
    ),
    _schema(
        "get_author",
        "One author: affiliation, ORCID, their papers in the library, their Wikipedia "
        "record and any links already found.",
        {"author_id": _INT},
        ["author_id"],
    ),
    _schema(
        "get_paper_links",
        "Citation links between this paper and others in the library, in both directions, "
        "plus how many referenced papers are not yet held.",
        {"paper_id": _INT},
        ["paper_id"],
    ),
    _schema(
        "pdf_sources",
        "Where a paper's full text can be got, and whether one is already stored. Check "
        "this before looking for a PDF: an open-access paper usually has a direct link "
        "recorded already, and a paper that already has a file needs nothing.",
        {"paper_id": _INT},
        ["paper_id"],
    ),
    _schema(
        "read_pdf",
        "Read the text of a paper's stored PDF, one page at a time. Use it to answer "
        "questions about what a paper actually says, rather than from its abstract.",
        {"paper_id": _INT, "page": _INT},
        ["paper_id"],
    ),
    _schema(
        "search_pdf",
        "Find a phrase inside a paper's PDF and see the surrounding text with its page "
        "number. Faster than reading pages one by one, and the way to check exact wording "
        "before highlighting.",
        {"paper_id": _INT, "query": _STR},
        ["paper_id", "query"],
    ),
    _schema(
        "pdf_outline",
        "The paper's section headings and their pages, taken from the document's own table "
        "of contents. Use it to find where something is discussed before reading: going "
        "straight to the right section beats scanning pages.",
        {"paper_id": _INT},
        ["paper_id"],
    ),
    _schema(
        "show_in_pdf",
        "Scroll the reader to a page, or to a passage you quote, so the user is looking at "
        "what you are describing. Use it whenever you refer to a specific part of a paper "
        "they have open. It only moves the view: to mark something permanently, use "
        "add_highlight.",
        {"paper_id": _INT, "page": _INT, "quote": _STR},
        ["paper_id"],
    ),
    _schema(
        "add_highlight",
        "Mark a passage in a paper's PDF by quoting it, optionally with a note. Quote the "
        "text exactly as it appears in the document — the quote is located in the file to "
        "work out where to draw the mark, so an approximation will not be found. Use "
        "search_pdf first if you are unsure of the wording.",
        {"paper_id": _INT, "quote": _STR, "comment": _STR, "page": _INT, "color": _STR},
        ["paper_id", "quote"],
    ),
    _schema(
        "list_highlights",
        "Passages already marked in a paper, with any notes on them and whether the user "
        "or you made each one.",
        {"paper_id": _INT},
        ["paper_id"],
    ),
    _schema(
        "attach_pdf",
        "Download a PDF from a URL and store it against a paper. The link must point at "
        "the file itself, not a landing page.\n\n"
        "Use only copies the publisher or author has put out openly: an arXiv or other "
        "preprint, a publisher's own open-access PDF, an institutional or subject "
        "repository, or the author's own page. Do not use sites that host paywalled "
        "papers without permission. If no legitimate copy exists, say so — that is a "
        "normal answer, and the user can add their own copy or read it through their "
        "institution.",
        {"paper_id": _INT, "url": {**_STR, "description": "Direct link to the PDF file."}},
        ["paper_id", "url"],
    ),
    _schema(
        "find_author_works",
        "Every paper OpenAlex attributes to this author, including ones the user does not "
        "hold, each marked with whether it is in the library.",
        {"author_id": _INT},
        ["author_id"],
    ),
    _schema(
        "find_wikipedia",
        "Resolve an author's Wikipedia page and biography through Wikidata, which confirms "
        "the page is about a person rather than a namesake place or work. Prefer this over "
        "web_search for Wikipedia.",
        {"author_id": _INT},
        ["author_id"],
    ),
    _schema(
        "web_search",
        "Search the web. Use it for what no bibliographic API knows, above all a "
        "researcher's own homepage or lab page.",
        {"query": _STR},
        ["query"],
    ),
    _schema(
        "fetch_url",
        "Fetch a page and return its text, the links it contains, and any images. Use it to "
        "check a page really is what you think before writing anything based on it — search "
        "snippets are not evidence. The links are how you find a PDF or a portrait: read "
        "`pdf_links` and `images` rather than guessing a URL. Set links false when you only "
        "want the prose.",
        {"url": _STR, "links": {"type": "boolean"}},
        ["url"],
    ),
    _schema(
        "lookup_identifier",
        "Look up a DOI, arXiv id or OpenAlex id in the metadata sources without adding "
        "anything. Use it to check what a paper actually is.",
        {"identifier": _STR},
        ["identifier"],
    ),
    _schema(
        "set_author_homepage",
        "Record an author's personal or institutional page. Only call this after fetch_url "
        "has confirmed the page names that author. Set verified true only when you have "
        "actually fetched it.",
        {"author_id": _INT, "url": _STR, "verified": {"type": "boolean"}},
        ["author_id", "url"],
    ),
    _schema(
        "set_author_bio",
        "Write a short biography for an author: a few sentences on their field, where they "
        "work and what they are known for. Use it when `find_wikipedia` returns nothing, "
        "which is the normal case for most researchers. Base it on pages you have actually "
        "fetched — their faculty page, lab page or department listing — and say nothing you "
        "could not point to. Do not speculate about someone from their name alone.",
        {"author_id": _INT, "bio": _STR,
         "source": {**_STR, "description": "Where it came from, e.g. a URL or 'assistant'."}},
        ["author_id", "bio"],
    ),
    _schema(
        "set_author_photo",
        "Store a portrait for an author from an image URL. Only use a photograph you are "
        "confident shows that person: one from their own faculty or lab page, or from a "
        "Wikipedia article about them. A photograph of the wrong person is worse than none, "
        "so if you are unsure, say so instead of guessing.",
        {"author_id": _INT, "url": {**_STR, "description": "Direct link to the image file."}},
        ["author_id", "url"],
    ),
    _schema(
        "add_note",
        "Add to the user's note on a paper. Their notes are their own thinking, so append "
        "rather than replace unless they ask otherwise.",
        {"paper_id": _INT, "text": _STR, "append": {"type": "boolean"}},
        ["paper_id", "text"],
    ),
    _schema(
        "set_paper_status",
        "Set reading status: unread, queued, skimmed, read or archived.",
        {"paper_id": _INT, "status": _STR},
        ["paper_id", "status"],
    ),
    _schema(
        "create_link",
        "Assert a typed link between two papers in the library, with a note saying why. "
        "This is the user's own reasoning made explicit, so only create a link you can "
        "justify from what you have actually read.",
        {
            "src_paper_id": _INT,
            "dst_paper_id": _INT,
            "type": {
                **_STR,
                "description": "cites, extends, uses_method, contradicts, background, "
                               "uses_data, supplement, replicates",
            },
            "note": _STR,
        },
        ["src_paper_id", "dst_paper_id", "type"],
    ),
    _schema(
        "read_skill",
        "Load the full instructions for a skill. The system prompt lists which skills "
        "exist and what each is for; read one before using the capability it describes, "
        "rather than guessing at it.",
        {"name": {**_STR, "description": "The skill name from the list."}},
        ["name"],
    ),
    _schema(
        "run_sql",
        "Run one read-only SELECT against the library database. Read the "
        "`database-queries` skill first: it carries the schema and the traps. Use this "
        "for counts, groupings and set questions the other tools cannot express.",
        {"query": {**_STR, "description": "A single SELECT or WITH ... SELECT statement."}},
        ["query"],
    ),
]


async def dispatch(
    conn: sqlite3.Connection, client: httpx.AsyncClient, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Run one tool. Never raises: the model must see failures as results."""
    try:
        if name in SYNC_TOOLS:
            return SYNC_TOOLS[name](conn, **args)
        if name in ASYNC_TOOLS:
            return await ASYNC_TOOLS[name](conn, client, **args)
        return {"error": f"There is no tool called '{name}'."}
    except TypeError as exc:
        return {"error": f"Wrong arguments for {name}: {exc}"}
    except Exception as exc:
        log.exception("tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


def preview(result: dict[str, Any], limit: int = 900) -> str:
    """A compact rendering of a tool's result, for the expandable detail.

    Trimmed rather than complete: the point is to let the user check what the
    assistant actually saw, not to reproduce the whole payload.
    """
    import json as _json

    trimmed: dict[str, Any] = {}
    for key, value in result.items():
        if key == "results" and isinstance(value, list):
            trimmed[key] = value[:5]
        elif key == "rows" and isinstance(value, list):
            trimmed[key] = value[:5]
        elif isinstance(value, str) and len(value) > 400:
            trimmed[key] = value[:400] + " …"
        else:
            trimmed[key] = value
    text = _json.dumps(trimmed, indent=1, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n…"


def summarise(name: str, result: dict[str, Any]) -> str:
    """One line for the UI, so tool activity is legible while it happens."""
    if "error" in result:
        return f"{name} failed: {str(result['error'])[:120]}"
    if "results" in result:
        return f"{name}: {result.get('shown', 0)} of {result.get('total', 0)}"
    if name in WRITE_TOOLS:
        return f"{name}: written"
    return name
