"""HTTP API. Local single-user service consumed by the React frontend."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, ingest, pdffind, pdftext, photos, store
from .sources.http import SourceError, make_client

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Breadcrumbs", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _apply_api_keys(conn: sqlite3.Connection) -> None:
    """Push stored bearer tokens into the HTTP layer.

    OpenAlex used to be open; it now meters every request against a daily
    budget, and an unkeyed process shares one small anonymous allowance with
    everything else on the same address.
    """
    from .sources.http import set_api_key

    set_api_key("api.openalex.org", db.get_setting(conn, "openalex_key").strip())


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    with db.session() as conn:
        _apply_api_keys(conn)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
class PreviewRequest(BaseModel):
    identifier: str = Field(min_length=1)
    # Sent when the user picked a typeahead suggestion. Used only to recover
    # if the identifier lookup fails outright.
    title: str | None = None


class RefetchRequest(BaseModel):
    token: str
    source: str


class SaveRequest(BaseModel):
    token: str | None = None
    identifier: str | None = None
    shelf_ids: list[int] = Field(default_factory=list)


class SettingsRequest(BaseModel):
    contact_email: str | None = None
    openalex_key: str | None = None
    semantic_scholar_key: str | None = None
    fetch_references: bool | None = None
    institution_proxy: str | None = None
    ai_openrouter_key: str | None = None
    ai_gemini_key: str | None = None
    ai_deepseek_key: str | None = None
    search_provider: str | None = None
    search_api_key: str | None = None
    assistant_provider: str | None = None
    assistant_model: str | None = None
    assistant_max_rounds: int | None = None
    assistant_thinking: str | None = None
    # OpenRouter only: pin the upstream provider serving the model. Blank lets
    # OpenRouter choose.
    assistant_route: str | None = None


class PaperPatch(BaseModel):
    status: str | None = None
    rating: int | None = None
    importance: int | None = None
    summary: str | None = None
    note: str | None = None
    favorite: bool | None = None


class LinkRequest(BaseModel):
    src_paper_id: int
    dst_paper_id: int
    type: str
    note: str | None = None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def read_settings(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    return db.redact(db.get_settings(conn))


@app.put("/api/settings")
def write_settings(
    body: SettingsRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "fetch_references":
            values[key] = "1" if value else "0"
        elif key in db.SECRET_KEYS and value == "":
            continue  # blank means "leave the stored key alone"
        else:
            values[key] = str(value)
    db.set_settings(conn, values)
    _apply_api_keys(conn)   # a new key takes effect without a restart
    return db.redact(db.get_settings(conn))


@app.post("/api/settings/test")
async def test_settings(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    """Probe each source so the settings page can show what actually works."""
    from .sources import crossref, openalex, s2, unpaywall

    settings = db.get_settings(conn)
    email = settings.get("contact_email", "").strip()
    key = settings.get("semantic_scholar_key", "").strip()
    probe = "10.1371/journal.pcbi.1006315"
    out: dict[str, Any] = {}

    async with make_client() as client:
        checks = {
            "openalex": lambda: openalex.fetch_by_doi(client, probe, email),
            "semantic_scholar": lambda: s2.fetch_paper(client, f"DOI:{probe}", key),
            "crossref": lambda: crossref.fetch_by_doi(client, probe, email),
            "unpaywall": lambda: unpaywall.fetch_oa(client, probe, email),
        }
        for name, call in checks.items():
            try:
                got = await call()
                out[name] = {"ok": bool(got), "detail": "reachable" if got else "no data returned"}
            except SourceError as exc:
                out[name] = {"ok": False, "detail": str(exc)}
            except Exception as exc:
                out[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    out["semantic_scholar"]["keyed"] = bool(key)
    if not email:
        out["unpaywall"]["detail"] = "needs a contact email"
    return out


# ---------------------------------------------------------------------------
# AI providers and tasks
# ---------------------------------------------------------------------------
@app.get("/api/ai/providers")
def ai_providers(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    """Providers the app knows, and whether a key is stored for each."""
    from .ai import providers as prov

    settings = db.get_settings(conn)
    return {
        "providers": [
            {
                "key": p.key,
                "label": p.label,
                "setting": p.setting,
                "docs": p.docs,
                "has_key": bool(settings.get(p.setting)),
            }
            for p in prov.PROVIDERS.values()
        ],
        "search": {
            "provider": settings.get("search_provider", "brave"),
            "has_key": bool(settings.get("search_api_key")),
        },
    }


@app.get("/api/ai/capabilities")
def ai_capabilities() -> dict[str, Any]:
    """What the assistant can do, for the settings page.

    Read straight from the registries rather than a maintained list, so the
    page cannot fall out of step with what is actually wired up.
    """
    from .ai import skills as skill_mod, tools as tool_mod

    skill_mod.reload()
    tools_out = []
    for schema in tool_mod.TOOL_SCHEMAS:
        fn = schema["function"]
        params = (fn.get("parameters") or {}).get("properties") or {}
        tools_out.append(
            {
                "name": fn["name"],
                "description": fn["description"],
                "is_write": fn["name"] in tool_mod.WRITE_TOOLS,
                "params": list(params),
            }
        )
    return {
        "tools": tools_out,
        "skills": [
            {"name": s.name, "description": s.description, "size": len(s.body)}
            for s in skill_mod.index()
        ],
    }


@app.get("/api/ai/model-endpoints")
async def ai_model_endpoints(
    provider: str, model: str, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Upstream providers serving one model, with their prices.

    Empty for everyone but OpenRouter, which is the only provider here that
    routes across other people's infrastructure.
    """
    from .ai import providers as prov

    p = prov.PROVIDERS.get(provider)
    if p is None:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    api_key = db.get_setting(conn, p.setting)
    try:
        async with make_client() as client:
            return {"endpoints": await prov.list_endpoints(client, provider, model, api_key)}
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/ai/models")
async def ai_models(
    provider: str, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Models this key can reach, asked of the provider rather than hardcoded."""
    from .ai import providers as prov

    p = prov.PROVIDERS.get(provider)
    if p is None:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    api_key = db.get_setting(conn, p.setting)
    try:
        async with make_client() as client:
            return {"models": await prov.list_models(client, provider, api_key)}
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/authors/{author_id}/links")
def author_links(
    author_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM author_links WHERE author_id = ?", (author_id,))
    return {"links": {r["kind"]: dict(r) for r in rows}}


# ---------------------------------------------------------------------------
# the assistant
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: int | None = None
    message: str = Field(min_length=1)
    # What the user is looking at, so "this paper" resolves without a guess.
    context: dict[str, Any] | None = None
    # Set when editing an earlier message: this one and everything after it are
    # removed before the new wording is answered.
    truncate_from_id: int | None = None


@app.get("/api/chat/sessions")
def chat_sessions(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id "
        "  AND m.role IN ('user','assistant')) AS turns "
        "FROM chat_sessions s ORDER BY s.updated_at DESC LIMIT 50"
    )
    return {"sessions": [dict(r) for r in rows]}


@app.post("/api/chat/sessions")
def new_chat_session(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    from .ai import agent

    return {"session_id": agent.create_session(conn)}


@app.get("/api/chat/sessions/{session_id}")
def chat_history(
    session_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """The transcript, with each answer's tool trail attached to it.

    Tool traffic is stored as its own rows, so it is regrouped here: a restored
    thread should show the same tool activity a live one does, rather than
    losing the record of what the assistant actually did.
    """
    from .ai import tools as tool_mod

    rows = conn.execute(
        "SELECT id, role, content, tool_calls, tool_call_id, tool_name, created_at "
        "FROM chat_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    messages: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []      # tools run since the last answer
    by_call: dict[str, dict[str, Any]] = {}

    for r in rows:
        if r["role"] == "user":
            messages.append(
                {"id": r["id"], "role": "user", "content": r["content"] or "",
                 "created_at": r["created_at"]}
            )
            pending, by_call = [], {}
        elif r["role"] == "assistant":
            for call in json.loads(r["tool_calls"] or "[]"):
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                entry = {
                    "name": fn.get("name"),
                    "args": args,
                    "status": "done",
                    "isWrite": fn.get("name") in tool_mod.WRITE_TOOLS,
                }
                pending.append(entry)
                if call.get("id"):
                    by_call[call["id"]] = entry
            if (r["content"] or "").strip():
                messages.append(
                    {"id": r["id"], "role": "assistant", "content": r["content"],
                     "tools": pending, "created_at": r["created_at"]}
                )
                pending, by_call = [], {}
        elif r["role"] == "tool":
            entry = by_call.get(r["tool_call_id"] or "")
            if entry is not None:
                try:
                    result = json.loads(r["content"] or "{}")
                except ValueError:
                    result = {}
                entry["summary"] = tool_mod.summarise(entry["name"] or "", result)
                entry["preview"] = tool_mod.preview(result)
                if result.get("error"):
                    entry["status"] = "failed"

    # A turn cut off mid-tool leaves activity with no answer; show it anyway so
    # the record matches what happened.
    if pending:
        messages.append({"id": None, "role": "assistant", "content": "",
                         "tools": pending, "interrupted": True})
    return {"messages": messages}


class SessionPatch(BaseModel):
    title: str


@app.patch("/api/chat/sessions/{session_id}")
def rename_chat_session(
    session_id: int, body: SessionPatch, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    conn.execute(
        "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
        (body.title.strip()[:80], session_id),
    )
    row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such conversation")
    return dict(row)


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(
    session_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, str]:
    conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    return {"status": "deleted"}


@app.post("/api/chat")
async def chat(body: ChatRequest) -> StreamingResponse:
    """Run one turn, streaming tool activity and the answer as they happen.

    Tool calls are streamed rather than summarised afterwards because the
    assistant can write to the database: watching which tool it reached for is
    how a wrong action gets caught while it is happening.
    """
    from .ai import agent

    session_id = body.session_id
    if session_id is None:
        with db.session() as conn:
            session_id = agent.create_session(conn)

    async def events() -> Any:
        yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n"
        try:
            async for name, data in agent.run_turn(
                session_id, body.message, body.context, body.truncate_from_id
            ):
                yield f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
        except Exception as exc:
            logging.exception("chat turn failed")
            payload = json.dumps({"message": f"{type(exc).__name__}: {exc}"})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# add flow: autocomplete -> search -> preview -> save
# ---------------------------------------------------------------------------
# Typeahead answers are cached in-process so backspacing over a query, or
# retyping one, costs nothing upstream. Small and short-lived on purpose.
_AC_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_AC_TTL = 300.0
_AC_MAX = 400


@app.get("/api/autocomplete")
async def autocomplete(
    q: str = Query(min_length=2, max_length=200),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    """Suggestions while typing. Returns at most 10, the upstream cap.

    A query that already looks like an identifier is answered locally, since
    there is nothing to suggest for a DOI.
    """
    import time as _time

    from .sources import s2

    query = q.strip()
    parsed = ingest.parse_identifier(query)
    if parsed is not None:
        return {"identifier": {"kind": parsed[0], "value": parsed[1]}, "results": []}

    key = query.casefold()
    hit = _AC_CACHE.get(key)
    if hit and _time.monotonic() - hit[0] < _AC_TTL:
        return {"identifier": None, "results": hit[1], "cached": True}

    try:
        async with make_client() as client:
            matches = await s2.autocomplete(
                client, query, db.get_setting(conn, "semantic_scholar_key")
            )
    except SourceError:
        # Typeahead must never surface an error banner; an empty list is fine.
        return {"identifier": None, "results": [], "degraded": True}

    for m in matches:
        m["in_library"] = store.find_paper_id(conn, m) is not None

    if len(_AC_CACHE) > _AC_MAX:
        _AC_CACHE.clear()
    _AC_CACHE[key] = (_time.monotonic(), matches)
    return {"identifier": None, "results": matches, "cached": False}



@app.get("/api/search")
async def search(
    q: str = Query(min_length=2), limit: int = 10, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    try:
        return {"results": await ingest.search_papers(conn, q, limit)}
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/preview")
async def preview(
    body: PreviewRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Fetch from every source and return the result. Writes nothing."""
    try:
        bundle = await ingest.fetch_bundle(conn, body.identifier, body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ingest.preview_payload(conn, bundle)


@app.get("/api/preview/stream")
async def preview_stream(
    identifier: str = Query(min_length=1), title: str | None = None
) -> StreamingResponse:
    """Server-sent events for one lookup, so panels fill in as sources answer.

    EventSource can only issue GET requests, hence query parameters rather than
    a body. Each source is reported the moment it lands.
    """

    async def events() -> Any:
        try:
            async for name, data in ingest.stream_bundle(identifier, title):
                yield f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
        except Exception as exc:  # a stream must always terminate cleanly
            logging.exception("preview stream failed")
            payload = json.dumps({"message": f"{type(exc).__name__}: {exc}", "kind": "crash"})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/preview/refetch")
async def refetch(
    body: RefetchRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Retry one source against the preview already on screen.

    The other sources' results are kept, so a throttled source can be retried
    on its own without redoing the whole lookup.
    """
    bundle = ingest.cached_bundle(body.token)
    if bundle is None:
        raise HTTPException(
            409, "This preview expired. Look the paper up again to refresh it."
        )
    try:
        bundle = await ingest.refetch_source(conn, bundle, body.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ingest.preview_payload(conn, bundle)


@app.post("/api/papers")
async def save_paper(
    body: SaveRequest,
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    """Commit a previewed bundle, or fetch and commit when given an identifier."""
    bundle = ingest.cached_bundle(body.token) if body.token else None
    if bundle is None:
        target = body.identifier or (body.token.split(":", 1)[1] if body.token else None)
        if not target:
            raise HTTPException(400, "Provide a preview token or an identifier.")
        try:
            bundle = await ingest.fetch_bundle(conn, target)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    result = await ingest.save_bundle(conn, bundle)
    for shelf_id in body.shelf_ids:
        conn.execute(
            "INSERT OR IGNORE INTO paper_shelves(paper_id, shelf_id) VALUES(?,?)",
            (result.paper_id, shelf_id),
        )
    return result.as_dict()


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------
@app.get("/api/papers")
def list_papers(
    shelf_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    sql = [
        "SELECT p.*, (SELECT group_concat(a.name, ', ') FROM paper_authors pa",
        "  JOIN authors a ON a.id = pa.author_id WHERE pa.paper_id = p.id",
        "  ORDER BY pa.position) AS authors",
        "FROM papers p",
    ]
    where, args = [], []
    if shelf_id is not None:
        sql.append("JOIN paper_shelves ps ON ps.paper_id = p.id AND ps.shelf_id = ?")
        args.append(shelf_id)
    if status:
        where.append("p.status = ?")
        args.append(status)
    if q:
        where.append("p.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?)")
        args.append(q)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY p.year DESC NULLS LAST, p.title")

    rows = conn.execute("\n".join(sql), args).fetchall()
    papers = []
    for r in rows:
        d = dict(r)
        d["authors"] = [a for a in (d.get("authors") or "").split(", ") if a]
        papers.append(d)
    return {"papers": papers}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such paper")
    paper = dict(row)

    # The cached portrait rides along, so hovering an author name can show a
    # face without a request per author.
    paper["authors"] = [
        dict(a)
        for a in conn.execute(
            "SELECT a.*, pa.position, pa.is_corresponding, "
            "  ap.thumbnail_url, ap.description AS profile_description, "
            "  ap.status AS profile_status "
            "FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
            "LEFT JOIN author_profiles ap ON ap.author_id = a.id "
            "WHERE pa.paper_id = ? ORDER BY pa.position",
            (paper_id,),
        )
    ]
    for author in paper["authors"]:
        author["institutions"] = [
            dict(i)
            for i in conn.execute(
                "SELECT i.* FROM authorship_institutions ai JOIN institutions i "
                "ON i.id = ai.institution_id WHERE ai.paper_id = ? AND ai.author_id = ?",
                (paper_id, author["id"]),
            )
        ]

    paper["topics"] = [
        dict(t)
        for t in conn.execute(
            "SELECT t.*, pt.score FROM paper_topics pt JOIN topics t ON t.id = pt.topic_id "
            "WHERE pt.paper_id = ? ORDER BY pt.score DESC",
            (paper_id,),
        )
    ]
    paper["links"] = [
        dict(l)
        for l in conn.execute(
            "SELECT l.*, "
            "  ps.title AS src_title, ps.year AS src_year, "
            "  pd.title AS dst_title, pd.year AS dst_year "
            "FROM links l JOIN papers ps ON ps.id = l.src_paper_id "
            "JOIN papers pd ON pd.id = l.dst_paper_id "
            "WHERE l.src_paper_id = ? OR l.dst_paper_id = ?",
            (paper_id, paper_id),
        )
    ]
    paper["shelves"] = [
        dict(s)
        for s in conn.execute(
            "SELECT s.* FROM paper_shelves ps JOIN shelves s ON s.id = ps.shelf_id "
            "WHERE ps.paper_id = ?",
            (paper_id,),
        )
    ]
    note = conn.execute("SELECT body FROM notes WHERE paper_id = ?", (paper_id,)).fetchone()
    paper["note"] = note["body"] if note else ""
    paper["unresolved_references"] = conn.execute(
        "SELECT COUNT(*) c FROM pending_links WHERE from_paper_id = ? "
        "AND resolved_paper_id IS NULL",
        (paper_id,),
    ).fetchone()["c"]
    return paper


@app.patch("/api/papers/{paper_id}")
def patch_paper(
    paper_id: int, body: PaperPatch, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    note = fields.pop("note", None)
    if fields:
        assigns = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE papers SET {assigns}, updated_at = datetime('now') WHERE id = ?",
            list(fields.values()) + [paper_id],
        )
    if note is not None:
        existing = conn.execute("SELECT id FROM notes WHERE paper_id = ?", (paper_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE notes SET body = ?, updated_at = datetime('now') WHERE id = ?",
                (note, existing["id"]),
            )
        else:
            conn.execute("INSERT INTO notes(paper_id, body) VALUES(?,?)", (paper_id, note))
    return get_paper(paper_id, conn)


@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    """Remove a paper and everything that existed only because of it.

    Authors and institutions reached only through this paper go too; ones
    shared with a paper you keep are left alone. Reference rows in other papers
    that pointed at this one go back to unresolved. See store.delete_paper.
    """
    try:
        removed = store.delete_paper(conn, paper_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "deleted", "removed": removed}


@app.delete("/api/papers/{paper_id}/references/{reference_id}")
def delete_reference(
    paper_id: int, reference_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Drop one row from a paper's reference list.

    Sources disagree, and sometimes one is simply wrong: OpenAlex fuses two
    works occasionally, and the result is a bibliography carrying references
    the paper never made. Nothing can arbitrate that automatically — you have
    read the paper — so removing a row by hand is the remedy.

    Only the reference row goes. If it resolved to a paper you hold, that paper
    and the link between them are untouched: this says "not cited here", not
    "delete that work".
    """
    row = conn.execute(
        "SELECT id, title, resolved_paper_id FROM pending_links WHERE id = ? AND from_paper_id = ?",
        (reference_id, paper_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "No such reference on this paper")

    conn.execute("DELETE FROM pending_links WHERE id = ?", (reference_id,))
    # The auto link this row created is a claim about the same citation, so it
    # goes with it. One you asserted by hand is your own and stays.
    unlinked = 0
    if row["resolved_paper_id"]:
        unlinked = conn.execute(
            "DELETE FROM links WHERE src_paper_id = ? AND dst_paper_id = ? AND origin = 'auto'",
            (paper_id, row["resolved_paper_id"]),
        ).rowcount
    return {"status": "deleted", "title": row["title"], "links_removed": unlinked}


@app.get("/api/papers/{paper_id}/pdf-options")
def pdf_options(
    paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Legal routes to the full text for this paper, best first.

    Open-access copies are downloadable directly. For a paywalled article the
    remaining routes are your institution's subscription or your own copy.
    """
    row = conn.execute(
        "SELECT id, doi, arxiv_id, pmid, url, is_oa, oa_pdf_url, pdf_path FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "No such paper")

    proxy = db.get_setting(conn, "institution_proxy").strip()
    options: list[dict[str, Any]] = []

    if row["pdf_path"]:
        options.append({"kind": "stored", "label": "Already in your library",
                        "url": f"/api/papers/{paper_id}/pdf", "auto": True})
    if row["arxiv_id"]:
        from .sources import arxiv
        options.append({"kind": "arxiv", "label": "arXiv preprint",
                        "url": arxiv.pdf_url(row["arxiv_id"]), "auto": True})
    if row["oa_pdf_url"]:
        options.append({"kind": "open_access", "label": "Open-access copy",
                        "url": row["oa_pdf_url"], "auto": True})
    if row["pmid"]:
        options.append({"kind": "pubmed", "label": "PubMed record",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{row['pmid']}/", "auto": False})
    if row["doi"]:
        publisher = f"https://doi.org/{row['doi']}"
        options.append({"kind": "publisher", "label": "Publisher page",
                        "url": publisher, "auto": False})
        if proxy:
            options.append({"kind": "institution",
                            "label": "Publisher via your institution",
                            "url": proxy + publisher, "auto": False})

    return {
        "paper_id": paper_id,
        "is_oa": bool(row["is_oa"]),
        "has_stored_pdf": bool(row["pdf_path"]),
        "options": options,
        "note": None if row["is_oa"] else (
            "No open-access copy is available. Use your institution's subscription, "
            "or upload a PDF you already have."
        ),
    }


@app.post("/api/papers/{paper_id}/pdf")
async def upload_pdf(
    paper_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    """Attach a PDF you already have to a paper in the library."""
    if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
        raise HTTPException(404, "No such paper")

    body = await file.read()
    if not body.startswith(b"%PDF"):
        raise HTTPException(400, "That file is not a PDF")
    if len(body) > 200 * 1024 * 1024:
        raise HTTPException(413, "PDF is larger than 200 MB")

    rel = f"pdfs/{paper_id}.pdf"
    (db.library_root() / rel).write_bytes(body)
    conn.execute("UPDATE papers SET pdf_path = ? WHERE id = ?", (rel, paper_id))
    return {"paper_id": paper_id, "pdf_path": rel, "bytes": len(body)}


@app.get("/api/papers/{paper_id}/pdf-search/stream")
async def pdf_search_stream(paper_id: int) -> StreamingResponse:
    """Hunt for this paper's PDF, reporting each step as it happens.

    A GET with a side effect, because EventSource cannot issue anything else
    and the search stores the file the moment it finds one. Same trade the
    preview stream makes.
    """

    async def events() -> Any:
        try:
            async for name, data in pdffind.search(paper_id):
                yield f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
        except Exception as exc:   # a stream must always terminate cleanly
            logging.exception("pdf search failed")
            payload = json.dumps({"message": f"{type(exc).__name__}: {exc}"})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/papers/{paper_id}/pdf")
def get_pdf(paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)) -> FileResponse:
    row = conn.execute("SELECT pdf_path FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None or not row["pdf_path"]:
        raise HTTPException(404, "No PDF stored for this paper")
    path = db.library_root() / row["pdf_path"]
    if not path.exists():
        raise HTTPException(404, "PDF file is missing from the library folder")
    return FileResponse(path, media_type="application/pdf")


# ---------------------------------------------------------------------------
# timeline, links, shelves
# ---------------------------------------------------------------------------
@app.get("/api/authors")
def list_authors(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    """Every author in the library, with the papers they appear on.

    The paper ids come back with the list so the sidebar can highlight an
    author's work on hover without a request per author.
    """
    rows = conn.execute(
        "SELECT a.id, a.name, a.orcid, a.affiliation, a.country, a.favorite, "
        "  COUNT(pa.paper_id) AS paper_count, "
        "  group_concat(pa.paper_id) AS paper_ids, "
        "  MIN(p.year) AS first_year, MAX(p.year) AS last_year, "
        "  (SELECT status FROM author_profiles ap WHERE ap.author_id = a.id) AS profile_status "
        "FROM authors a "
        "JOIN paper_authors pa ON pa.author_id = a.id "
        "JOIN papers p ON p.id = pa.paper_id "
        "GROUP BY a.id "
        "ORDER BY paper_count DESC, a.name"
    ).fetchall()

    authors = []
    for r in rows:
        d = dict(r)
        d["paper_ids"] = [int(x) for x in (d.pop("paper_ids") or "").split(",") if x]
        authors.append(d)
    return {"authors": authors}


@app.get("/api/authors/{author_id}")
def get_author(author_id: int, conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such author")
    author = dict(row)

    author["papers"] = [
        dict(p)
        for p in conn.execute(
            "SELECT p.id, p.title, p.year, p.venue, p.citation_count, pa.position, "
            "  pa.is_corresponding "
            "FROM paper_authors pa JOIN papers p ON p.id = pa.paper_id "
            "WHERE pa.author_id = ? ORDER BY p.year",
            (author_id,),
        )
    ]
    author["institutions"] = [
        dict(i)
        for i in conn.execute(
            "SELECT DISTINCT i.* FROM authorship_institutions ai "
            "JOIN institutions i ON i.id = ai.institution_id WHERE ai.author_id = ?",
            (author_id,),
        )
    ]
    profile = conn.execute(
        "SELECT * FROM author_profiles WHERE author_id = ?", (author_id,)
    ).fetchone()
    author["profile"] = dict(profile) if profile else None
    return author


def _profile_payload(row: dict[str, Any], author_id: int, cached: bool) -> dict[str, Any]:
    """Shape a stored profile for the client.

    A portrait you supplied wins over one Wikipedia offers, and `bio` is the
    field the panel shows regardless of which source filled it.
    """
    out = dict(row)
    out["cached"] = cached
    if row.get("custom_image"):
        out["portrait_url"] = f"/api/authors/{author_id}/photo"
        out["portrait_source"] = row.get("custom_image_source") or "upload"
    else:
        out["portrait_url"] = row.get("thumbnail_url")
        out["portrait_full_url"] = row.get("image_url")
        out["portrait_source"] = "wikipedia" if row.get("thumbnail_url") else None
    out["facts"] = json.loads(row["facts"]) if row.get("facts") else None
    out["bio"] = row.get("bio") or row.get("extract")
    out["bio_source"] = row.get("bio_source") or ("wikipedia" if row.get("extract") else None)
    return out


@app.get("/api/authors/{author_id}/profile")
async def author_profile(
    author_id: int,
    refresh: bool = False,
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    """Biography and portrait for an author, cached after the first lookup.

    A miss is cached too: most researchers have no Wikipedia page, and without
    that the same fruitless search would run on every visit.
    """
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such author")

    if not refresh:
        cached = conn.execute(
            "SELECT * FROM author_profiles WHERE author_id = ?", (author_id,)
        ).fetchone()
        if cached:
            return _profile_payload(dict(cached), author_id, cached=True)

    from .sources import wikipedia

    try:
        async with make_client() as client:
            found = await wikipedia.fetch_profile(client, row["name"], row["affiliation"])
    except SourceError as exc:
        found = {"status": "error", "detail": str(exc)}

    # A Wikipedia hit fills the bio, but only where one is not already written:
    # a bio the assistant assembled from an author's own pages should not be
    # overwritten by a later Wikipedia sync.
    conn.execute(
        "INSERT INTO author_profiles(author_id, status, title, url, description, extract, "
        "  thumbnail_url, image_url, detail, fetched_at, bio, bio_source, bio_updated_at, "
        "  facts) VALUES(?,?,?,?,?,?,?,?,?, datetime('now'), ?, ?, datetime('now'), ?) "
        "ON CONFLICT(author_id) DO UPDATE SET status=excluded.status, title=excluded.title, "
        "  url=excluded.url, description=excluded.description, extract=excluded.extract, "
        "  thumbnail_url=excluded.thumbnail_url, image_url=excluded.image_url, "
        "  detail=excluded.detail, fetched_at=excluded.fetched_at, "
        "  facts = excluded.facts, "
        "  bio = COALESCE(author_profiles.bio, excluded.bio), "
        "  bio_source = COALESCE(author_profiles.bio_source, excluded.bio_source), "
        "  bio_updated_at = COALESCE(author_profiles.bio_updated_at, excluded.bio_updated_at)",
        (
            author_id, found["status"], found.get("title"), found.get("url"),
            found.get("description"), found.get("extract"), found.get("thumbnail_url"),
            found.get("image_url"), found.get("detail"),
            found.get("extract"), "wikipedia" if found.get("extract") else None,
            json.dumps(found.get("facts")) if found.get("facts") else None,
        ),
    )
    stored = conn.execute(
        "SELECT * FROM author_profiles WHERE author_id = ?", (author_id,)
    ).fetchone()
    return _profile_payload(dict(stored), author_id, cached=False)


class PhotoUrlRequest(BaseModel):
    url: str = Field(min_length=4)


@app.get("/api/authors/{author_id}/photo")
def get_author_photo(
    author_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> FileResponse:
    path = photos.stored_path(conn, author_id)
    if path is None:
        raise HTTPException(404, "No portrait stored for this author")
    return FileResponse(path)


class AuthorPatch(BaseModel):
    favorite: bool | None = None


@app.patch("/api/authors/{author_id}")
def patch_author(
    author_id: int, body: AuthorPatch, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Your own marks on an author. Nothing here comes from a source."""
    fields = body.model_dump(exclude_unset=True)
    if fields:
        assigns = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(
            f"UPDATE authors SET {assigns}, updated_at = datetime('now') WHERE id = ?",
            list(fields.values()) + [author_id],
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "No such author")
    return get_author(author_id, conn)


@app.post("/api/authors/{author_id}/photo")
async def upload_author_photo(
    author_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM authors WHERE id = ?", (author_id,)).fetchone() is None:
        raise HTTPException(404, "No such author")
    result = photos.save_bytes(conn, author_id, await file.read(), source="upload")
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/authors/{author_id}/photo-url")
async def set_author_photo_url(
    author_id: int, body: PhotoUrlRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Fetch an image from a URL and cache it, rather than linking to it."""
    if conn.execute("SELECT 1 FROM authors WHERE id = ?", (author_id,)).fetchone() is None:
        raise HTTPException(404, "No such author")
    async with make_client() as client:
        result = await photos.save_from_url(conn, client, author_id, body.url)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.delete("/api/authors/{author_id}/photo")
def delete_author_photo(
    author_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    return photos.clear(conn, author_id)


@app.get("/api/authors/{author_id}/facts")
async def author_facts(
    author_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """A profile composed from every source that knows this person.

    No single source is enough. ORCID is authoritative but its biography field
    is usually blank; Wikidata has clean structured facts but only for notable
    people; OpenAlex covers every author but has no prose. Each contributes
    what it is good for, labelled with where it came from.
    """
    from .sources import openalex, orcid as orcid_src

    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such author")

    email = db.get_setting(conn, "contact_email")
    out: dict[str, Any] = {"author_id": author_id, "name": row["name"], "sources": []}

    async with make_client() as client:
        if row["orcid"]:
            try:
                person = await orcid_src.fetch_person(client, row["orcid"])
                employments = await orcid_src.fetch_employments(client, row["orcid"])
                if person or employments:
                    out["orcid"] = {
                        "id": row["orcid"],
                        **(person or {}),
                        "employments": employments,
                    }
                    out["sources"].append("orcid")
            except SourceError:
                pass

        if row["openalex_id"]:
            try:
                career = await openalex.fetch_author(client, row["openalex_id"], email)
                if career:
                    out["openalex"] = career
                    out["sources"].append("openalex")
            except SourceError:
                pass

    profile = conn.execute(
        "SELECT status, title, url, description, extract, bio, bio_source "
        "FROM author_profiles WHERE author_id = ?",
        (author_id,),
    ).fetchone()
    if profile and profile["status"] == "found":
        out["wikipedia"] = dict(profile)
        out["sources"].append("wikipedia")

    return out


@app.get("/api/authors/{author_id}/works")
async def author_works(
    author_id: int, limit: int = 100, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """This author's full output according to OpenAlex, not just what you hold.

    Each entry is marked with whether it is already in the library, so the
    panel can offer a preview of the ones that are not.
    """
    from .sources import openalex

    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such author")
    if not row["openalex_id"]:
        return {
            "works": [], "profile": None,
            "detail": "This author has no OpenAlex id, so their output cannot be listed.",
        }

    email = db.get_setting(conn, "contact_email")
    try:
        async with make_client() as client:
            profile = await openalex.fetch_author(client, row["openalex_id"], email)
            works = await openalex.fetch_author_works(client, row["openalex_id"], email, limit)
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc

    for w in works:
        existing = store.find_paper_id(conn, w)
        w["in_library"] = existing is not None
        w["paper_id"] = existing

    return {"works": works, "profile": profile, "detail": None}


async def _geocode_missing(conn: sqlite3.Connection) -> int:
    """Fill in coordinates for institutions that have none yet.

    Done lazily rather than at import: most institutions are never put on a
    map, and this keeps adding a paper fast. `geo_checked` records the attempt
    so an institution OpenAlex cannot place is not retried on every view.
    """
    from .sources import openalex

    rows = conn.execute(
        "SELECT openalex_id FROM institutions "
        "WHERE lat IS NULL AND geo_checked IS NULL AND openalex_id IS NOT NULL LIMIT 100"
    ).fetchall()
    if not rows:
        return 0

    email = db.get_setting(conn, "contact_email")
    try:
        async with make_client() as client:
            found = await openalex.fetch_institutions(
                client, [r["openalex_id"] for r in rows], email
            )
    except SourceError:
        return 0

    filled = 0
    for row in rows:
        geo = found.get(row["openalex_id"])
        conn.execute(
            "UPDATE institutions SET lat = ?, lon = ?, city = COALESCE(?, city), "
            "  country_code = COALESCE(?, country_code), geo_checked = datetime('now') "
            "WHERE openalex_id = ?",
            (
                (geo or {}).get("lat"), (geo or {}).get("lon"), (geo or {}).get("city"),
                (geo or {}).get("country_code"), row["openalex_id"],
            ),
        )
        if geo and geo.get("lat") is not None:
            filled += 1
    return filled


@app.get("/api/map")
async def affiliation_map(
    scope: str = "library",
    id: int | None = None,
    conn: sqlite3.Connection = Depends(db.get_conn),
) -> dict[str, Any]:
    """Affiliation locations, grouped so one institution is one marker.

    `scope` is library, author or paper. For an author, the institutions on
    their most recent paper are marked current, which is the closest thing the
    data supports to "where they are now".
    """
    await _geocode_missing(conn)

    base = (
        "SELECT i.id, i.name, i.city, i.country_code, i.lat, i.lon, "
        "  a.id AS author_id, a.name AS author_name, p.id AS paper_id, p.year "
        "FROM authorship_institutions ai "
        "JOIN institutions i ON i.id = ai.institution_id "
        "JOIN authors a ON a.id = ai.author_id "
        "JOIN papers p ON p.id = ai.paper_id "
        "WHERE i.lat IS NOT NULL "
    )
    args: list[Any] = []
    if scope == "author" and id is not None:
        base += "AND ai.author_id = ? "
        args.append(id)
    elif scope == "paper" and id is not None:
        base += "AND ai.paper_id = ? "
        args.append(id)

    rows = conn.execute(base, args).fetchall()

    # The latest year an author appears with each institution decides which of
    # their affiliations counts as current.
    latest_year = None
    if scope == "author" and id is not None and rows:
        latest_year = max((r["year"] or 0) for r in rows)

    points: dict[int, dict[str, Any]] = {}
    for r in rows:
        point = points.setdefault(
            r["id"],
            {
                "institution_id": r["id"],
                "name": r["name"],
                "city": r["city"],
                "country_code": r["country_code"],
                "lat": r["lat"],
                "lon": r["lon"],
                "authors": [],
                "papers": set(),
                "current": False,
            },
        )
        if r["author_name"] not in [a["name"] for a in point["authors"]]:
            point["authors"].append({"id": r["author_id"], "name": r["author_name"]})
        point["papers"].add(r["paper_id"])
        if latest_year is not None and (r["year"] or 0) == latest_year:
            point["current"] = True

    out = []
    for point in points.values():
        point["papers"] = len(point["papers"])
        point["author_count"] = len(point["authors"])
        out.append(point)
    out.sort(key=lambda p: (-p["author_count"], p["name"]))

    missing = conn.execute(
        "SELECT COUNT(*) c FROM institutions WHERE lat IS NULL"
    ).fetchone()["c"]
    return {"scope": scope, "id": id, "points": out, "unplaced": missing}


# ---------------------------------------------------------------------------
# highlights and anchored notes
# ---------------------------------------------------------------------------
class HighlightRect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class HighlightRequest(BaseModel):
    page: int
    rects: list[HighlightRect]
    quoted: str | None = None
    comment: str | None = None
    color: str | None = None
    page_width: float | None = None
    page_height: float | None = None


class HighlightPatch(BaseModel):
    comment: str | None = None
    color: str | None = None


@app.get("/api/papers/{paper_id}/references")
def paper_references(
    paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """What this paper cites.

    Every reference is stored at import as an identifier plus a title, whether
    or not the other side is in the library. So the list is complete, and each
    entry says whether you hold that paper — which is what makes it useful for
    deciding what to add next.

    For the other direction — papers here that cite *this* one — see
    `cited_by`, which reads the same rows from the far side. That view covers
    your library only, which is the whole of what the graph draws.
    """
    if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
        raise HTTPException(404, "No such paper")

    rows = conn.execute(
        "SELECT pl.id, pl.doi, pl.openalex_id, pl.arxiv_id, pl.title, pl.year, "
        "  pl.authors_blob, pl.citation_count, pl.resolved_paper_id, "
        "  p.title AS held_title "
        "FROM pending_links pl "
        "LEFT JOIN papers p ON p.id = pl.resolved_paper_id "
        "WHERE pl.from_paper_id = ? "
        "ORDER BY pl.citation_count DESC NULLS LAST, pl.year DESC NULLS LAST",
        (paper_id,),
    ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["in_library"] = d["resolved_paper_id"] is not None
        items.append(d)
    return {
        "items": items,
        "total": len(items),
        "in_library": sum(1 for i in items if i["in_library"]),
    }


@app.get("/api/papers/{paper_id}/cited-by")
def paper_cited_by(
    paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Papers in your library whose bibliography names this one.

    Read straight off the reference rows other papers already stored, so it
    needs no fetching of its own and stays correct as the library grows. It is
    deliberately limited to what you hold: the full list of citing work runs to
    tens of thousands for a well-known paper, and a truncated version of it
    would be an arbitrary sample rather than an answer.
    """
    if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
        raise HTTPException(404, "No such paper")

    rows = conn.execute(
        "SELECT p.id, p.title, p.year, p.venue, p.citation_count, "
        "  (SELECT group_concat(a.name, ', ') FROM paper_authors pa "
        "   JOIN authors a ON a.id = pa.author_id WHERE pa.paper_id = p.id "
        "   ORDER BY pa.position) AS authors_blob "
        "FROM pending_links pl "
        "JOIN papers p ON p.id = pl.from_paper_id "
        "WHERE pl.resolved_paper_id = ? AND pl.from_paper_id != ? "
        "ORDER BY p.year DESC NULLS LAST, p.title",
        (paper_id, paper_id),
    ).fetchall()

    items = [dict(r) | {"in_library": True} for r in rows]
    return {"items": items, "total": len(items)}


@app.get("/api/papers/{paper_id}/outline")
def paper_outline(
    paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """The PDF's own section headings, when it carries them."""
    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        raise HTTPException(404, "No PDF stored for this paper")
    entries = pdftext.outline(path)
    return {
        "outline": entries,
        "note": None if entries else "This PDF carries no embedded table of contents.",
    }


@app.get("/api/papers/{paper_id}/highlights")
def list_highlights(
    paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM highlights WHERE paper_id = ? ORDER BY page, id", (paper_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rects"] = json.loads(d["rects"] or "[]")
        out.append(d)
    return {"highlights": out}


@app.post("/api/papers/{paper_id}/highlights")
def create_highlight(
    paper_id: int, body: HighlightRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
        raise HTTPException(404, "No such paper")
    cur = conn.execute(
        "INSERT INTO highlights(paper_id, page, rects, quoted, comment, color, "
        "  page_width, page_height, source) VALUES(?,?,?,?,?,?,?,?, 'user')",
        (
            paper_id, body.page,
            json.dumps([r.model_dump() for r in body.rects]),
            # A selection dragged across a line break carries the typesetter's
            # hyphen with it — "clus- tering". The rectangles are unaffected;
            # only the text stored alongside them needs mending.
            pdftext.join_broken_words(body.quoted or "") or None,
            body.comment, body.color or "#fde047",
            body.page_width, body.page_height,
        ),
    )
    row = conn.execute("SELECT * FROM highlights WHERE id = ?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["rects"] = json.loads(d["rects"] or "[]")
    return d


@app.patch("/api/highlights/{highlight_id}")
def update_highlight(
    highlight_id: int, body: HighlightPatch, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Nothing to update")
    assigns = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE highlights SET {assigns} WHERE id = ?",
        list(fields.values()) + [highlight_id],
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "No such highlight")
    row = conn.execute("SELECT * FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
    d = dict(row)
    d["rects"] = json.loads(d["rects"] or "[]")
    return d


@app.delete("/api/highlights/{highlight_id}")
def delete_highlight(
    highlight_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, str]:
    conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
    return {"status": "deleted"}


@app.get("/api/papers/{paper_id}/pdf-text")
def paper_pdf_text(
    paper_id: int, page: int | None = None, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Extracted text, for search and for answering questions about the file."""
    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        raise HTTPException(404, "No PDF stored for this paper")
    pages = pdftext.extract_text(path)
    if page is not None:
        pages = [p for p in pages if p["page"] == page]
    return {"pages": pages, "page_count": len(pages)}


@app.get("/api/timeline")
def timeline(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    """Papers with a year, plus resolved links, for the timeline view."""
    rows = conn.execute(
        "SELECT p.id, p.title, p.year, p.month, p.venue, p.status, p.citation_count, "
        "  p.favorite, (p.pdf_path IS NOT NULL) AS has_pdf, "
        "  (SELECT group_concat(a.name, ', ') FROM paper_authors pa "
        "   JOIN authors a ON a.id = pa.author_id WHERE pa.paper_id = p.id ORDER BY pa.position) "
        "   AS authors, "
        "  (SELECT s.kind FROM paper_shelves ps JOIN shelves s ON s.id = ps.shelf_id "
        "   WHERE ps.paper_id = p.id ORDER BY s.sort_order LIMIT 1) AS kind "
        "FROM papers p WHERE p.year IS NOT NULL ORDER BY p.year, p.month"
    ).fetchall()
    papers = []
    for r in rows:
        d = dict(r)
        d["authors"] = [a for a in (d.get("authors") or "").split(", ") if a]
        papers.append(d)
    links = [
        dict(l)
        for l in conn.execute(
            "SELECT id, src_paper_id, dst_paper_id, type, intent, is_influential, "
            "origin, confirmed FROM links"
        )
    ]
    return {"papers": papers, "links": links}


@app.post("/api/links")
def create_link(
    body: LinkRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    if body.src_paper_id == body.dst_paper_id:
        raise HTTPException(400, "A paper cannot link to itself")
    try:
        cur = conn.execute(
            "INSERT INTO links(src_paper_id, dst_paper_id, type, note, origin, confirmed) "
            "VALUES(?,?,?,?, 'manual', 1)",
            (body.src_paper_id, body.dst_paper_id, body.type, body.note),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, f"Link already exists or type is unknown: {exc}") from exc
    return {"id": cur.lastrowid}


@app.patch("/api/links/{link_id}")
def update_link(
    link_id: int, body: dict[str, Any], conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, str]:
    allowed = {k: v for k, v in body.items() if k in ("type", "note", "confirmed")}
    if not allowed:
        raise HTTPException(400, "Nothing to update")
    assigns = ", ".join(f"{k} = ?" for k in allowed)
    conn.execute(f"UPDATE links SET {assigns} WHERE id = ?", list(allowed.values()) + [link_id])
    return {"status": "ok"}


@app.delete("/api/links/{link_id}")
def delete_link(link_id: int, conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, str]:
    conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
    return {"status": "deleted"}


@app.get("/api/link-types")
def link_types(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    return {"types": [dict(r) for r in conn.execute("SELECT * FROM link_types")]}


@app.get("/api/shelves")
def list_shelves(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM paper_shelves ps WHERE ps.shelf_id = s.id) AS count "
        "FROM shelves s ORDER BY s.sort_order, s.name"
    ).fetchall()
    return {"shelves": [dict(r) for r in rows]}


@app.post("/api/shelves/{shelf_id}/papers/{paper_id}")
def add_to_shelf(
    shelf_id: int, paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, str]:
    conn.execute(
        "INSERT OR IGNORE INTO paper_shelves(paper_id, shelf_id) VALUES(?,?)", (paper_id, shelf_id)
    )
    return {"status": "ok"}


@app.delete("/api/shelves/{shelf_id}/papers/{paper_id}")
def remove_from_shelf(
    shelf_id: int, paper_id: int, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, str]:
    conn.execute(
        "DELETE FROM paper_shelves WHERE paper_id = ? AND shelf_id = ?", (paper_id, shelf_id)
    )
    return {"status": "ok"}


@app.post("/api/resolve-links")
def resolve_all(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, int]:
    """Re-scan every pending row. Useful after a bulk import."""
    return {"links_created": store.resolve_links(conn)}


class WipeRequest(BaseModel):
    """Typing the phrase is the confirmation; a stray click cannot do this."""

    confirm: str


@app.post("/api/library/wipe")
def wipe_library(
    body: WipeRequest, conn: sqlite3.Connection = Depends(db.get_conn)
) -> dict[str, Any]:
    """Delete every paper, author, link, note and highlight.

    Settings, API keys and conversations are kept: they are not the library,
    and losing them would make starting again harder rather than cleaner.
    Stored PDFs and portraits go with the records that referenced them.
    """
    if body.confirm.strip().lower() != "delete my library":
        raise HTTPException(400, "Type 'delete my library' to confirm.")

    before = {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("papers", "authors", "links", "pending_links", "highlights", "notes")
    }

    # papers and authors cascade to everything hanging off them; the rest are
    # cleared explicitly because they have no parent row.
    for table in ("highlights", "notes", "pending_links", "links", "paper_authors",
                  "authorship_institutions", "paper_shelves", "paper_topics",
                  "author_profiles", "author_links", "papers", "authors",
                  "institutions", "topics"):
        conn.execute(f"DELETE FROM {table}")

    root = db.library_root()
    removed = 0
    for folder in ("pdfs", "authors"):
        directory = root / folder
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
                    removed += 1

    return {"status": "wiped", "deleted": before, "files_removed": removed}


@app.get("/api/stats")
def stats(conn: sqlite3.Connection = Depends(db.get_conn)) -> dict[str, Any]:
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "papers": one("SELECT COUNT(*) FROM papers"),
        "authors": one("SELECT COUNT(*) FROM authors"),
        "links": one("SELECT COUNT(*) FROM links"),
        "pending": one("SELECT COUNT(*) FROM pending_links WHERE resolved_paper_id IS NULL"),
        "with_pdf": one("SELECT COUNT(*) FROM papers WHERE pdf_path IS NOT NULL"),
        "library_path": str(db.library_root()),
    }


# ---------------------------------------------------------------------------
# the built frontend, when there is one
# ---------------------------------------------------------------------------
# Mounted last, so every /api route above is matched first — a mount at "/"
# otherwise swallows the lot. Optional by design: in development Vite serves
# the UI on its own port with hot reload, and this directory does not exist
# until `make build`. With it, the backend alone serves the whole app.
def _frontend_dist() -> Path:
    override = os.environ.get("BREADCRUMBS_STATIC")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _pages_site() -> Path:
    """Where `make pages` puts the published copy."""
    override = os.environ.get("BREADCRUMBS_PAGES")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "site"


_PAGES = _pages_site()
if (_PAGES / "index.html").is_file():
    # The read-only build, served alongside the real app so it can be checked
    # before it is published. It reads its own frozen JSON and talks to no API,
    # so what appears here is exactly what a visitor to the published site
    # would get — including everything deliberately missing from it.
    #
    # Mounted before "/" for the same reason the API routes are: a mount at the
    # root matches everything after it.
    @app.get("/readonly", include_in_schema=False)
    def _readonly_slash() -> RedirectResponse:
        """Send /readonly to /readonly/.

        The published bundle is built with a relative base so it can sit at any
        path. Relative asset URLs resolve against the directory of the current
        URL, so without the trailing slash "./assets/x.js" resolves to
        "/assets/x.js" — the development bundle, or nothing at all.
        """
        return RedirectResponse("/readonly/", status_code=308)

    app.mount("/readonly", StaticFiles(directory=_PAGES, html=True), name="readonly")


_DIST = _frontend_dist()
if (_DIST / "index.html").is_file():
    # html=True serves index.html for "/", which is all the routing this app
    # needs — the reader opens as "?paper=12" rather than a path.
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
