"""The assistant loop.

Deliberately not built on an agent framework. The loop below is the whole
orchestration: send messages plus tool schemas, run whatever tools come back,
append the results, repeat. Everything that decides quality lives in the tool
descriptions and the system prompt, not in control flow, so a framework would
add dependency surface and hide the exchange without removing any complexity.

Events stream to the browser as the turn runs. That is not decoration: the
assistant writes to the database, so seeing which tool it reached for and what
came back is how a wrong action gets caught while it happens.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, AsyncIterator

from .. import db
from ..sources.http import SourceError, make_client
from . import providers, skills, tools

log = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 12
TURN_TIMEOUT_S = 300.0

SYSTEM_PROMPT = """\
You are the assistant inside Breadcrumbs, a personal tool for studying research \
literature. The user is a researcher building a curated library of papers and the \
links between them.

How to work:

- Answer from the library first. `search_library`, `list_papers` and `list_authors` \
describe what the user actually holds; the web is for what those cannot answer.
- Resolve names to ids before acting. Never guess an id.
- Check before you write. If you are about to record a URL, fetch it first and \
confirm it says what you think. A search snippet is not evidence.
- You cannot add papers to the library. That is the user's decision and the whole \
point of the tool. To suggest one, write a link with a `paper:` target and the \
identifier: `[Learning representations by back-propagating errors](paper:10.1038/323533a0)`. \
That renders as a button which opens the paper's preview for them to accept or \
ignore. Use it for every paper you suggest, as many as the answer needs, in the \
sentence where you mention it. Never imply a suggested paper is now held.
- You may freely write notes, links, reading statuses and author homepages.
- Suggest papers with `paper:` links wherever they come up — in a list of \
candidates, in an answer about what to read next, or when you happen to mention \
one the user does not hold. It costs them nothing to ignore.
- Say what you did, briefly. If a tool failed or you could not verify something, \
say so plainly instead of presenting a guess as fact.
- Be concise. This is a side panel, not an essay.

Formatting: your replies are rendered as Markdown, so use it — **bold** for the thing that matters, bullet lists for several items, tables for comparisons, and `code` for identifiers, DOIs and field names. Maths is rendered with KaTeX: write inline maths as $\\Delta w_{ij} = \\eta \\, \\delta_j x_i$ and displayed maths as $$W \\leftarrow W - \\eta \\nabla_W L$$. Use it whenever a learning rule or update is clearer as a formula than as prose. Keep headings rare: the panel is narrow."""


def _context_block(context: dict[str, Any] | None) -> str:
    """What the user is looking at, sent with every message.

    Passed as context rather than exposed as a tool: it is always accurate,
    costs nothing, and saves a round trip on almost every question.
    """
    if not context:
        return ""
    bits = []
    if context.get("tab"):
        bits.append(f"They are on the {context['tab']} view.")
    if context.get("paper"):
        p = context["paper"]
        bits.append(f"Selected paper: id {p.get('id')}, \"{p.get('title')}\" ({p.get('year')}).")
    if context.get("author"):
        a = context["author"]
        bits.append(f"Selected author: id {a.get('id')}, {a.get('name')}.")
    if context.get("visible_papers"):
        seen = context["visible_papers"][:12]
        bits.append("Papers in view: " + "; ".join(f"{p['id']}:{p['title'][:48]}" for p in seen))

    # In the reader, the page in front of them is included outright. Calling a
    # tool to fetch what the user is already looking at wastes a round trip and
    # makes the assistant look slow for no reason.
    reading = context.get("reading")
    if reading and reading.get("outline"):
        # The paper's shape, so it knows where things are without reading
        # anything. Cheap: a couple of dozen lines for a whole document.
        headings = "; ".join(
            f"{'  ' * (h['level'] - 1)}{h['title']} (p{h['page']})"
            for h in reading["outline"][:40]
        )
        bits.append(f"Sections of this paper: {headings}")
    if reading and reading.get("text"):
        bits.append(
            f"They are reading page {reading['page']} of {reading['page_count']}. "
            "Its full text follows, so answer from it directly rather than calling "
            "read_pdf for this page. Use search_pdf or read_pdf only for other pages.\n"
            f"--- page {reading['page']} ---\n{reading['text'][:9000]}\n--- end of page ---"
        )

    selection = context.get("selection")
    if selection and selection.get("text"):
        bits.append(
            f"They have attached this passage from page {selection.get('page')}, and a "
            f"question like \"explain this\" refers to it:\n\"{selection['text'][:2000]}\""
        )
    return "\n".join(bits)


# ---------------------------------------------------------------------------
# conversation storage
# ---------------------------------------------------------------------------
def create_session(conn: sqlite3.Connection, title: str | None = None) -> int:
    cur = conn.execute("INSERT INTO chat_sessions(title) VALUES(?)", (title,))
    return int(cur.lastrowid)


def load_history(conn: sqlite3.Connection, session_id: int, limit: int = 40) -> list[dict[str, Any]]:
    """Recent turns, oldest first, in the shape the provider expects."""
    rows = conn.execute(
        "SELECT * FROM (SELECT * FROM chat_messages WHERE session_id = ? "
        "ORDER BY id DESC LIMIT ?) ORDER BY id",
        (session_id, limit),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        if r["role"] == "tool":
            out.append(
                {"role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"] or ""}
            )
        elif r["role"] == "assistant" and r["tool_calls"]:
            out.append(
                {
                    "role": "assistant",
                    "content": r["content"] or "",
                    "tool_calls": json.loads(r["tool_calls"]),
                }
            )
        else:
            out.append({"role": r["role"], "content": r["content"] or ""})

    # A tool result whose matching call fell outside the window would be
    # rejected by the provider, so drop any orphans at the front.
    while out and out[0]["role"] == "tool":
        out.pop(0)

    # An interrupted turn can leave tool calls with no results: the user sent a
    # new message while a tool was still running. Providers reject a history
    # like that, so drop any call whose answer never arrived.
    answered = {m.get("tool_call_id") for m in out if m["role"] == "tool"}
    cleaned: list[dict[str, Any]] = []
    for message in out:
        calls = message.get("tool_calls") if message["role"] == "assistant" else None
        if calls:
            kept = [c for c in calls if c.get("id") in answered]
            if not kept:
                # Nothing came back at all; keep any prose it managed first.
                if (message.get("content") or "").strip():
                    cleaned.append({"role": "assistant", "content": message["content"]})
                continue
            message = {**message, "tool_calls": kept}
        cleaned.append(message)
    return cleaned


def truncate_from(conn: sqlite3.Connection, session_id: int, message_id: int) -> int:
    """Drop this message and everything after it.

    Used when a message is edited: the replies that followed were answers to
    the old wording, so keeping them would leave the thread contradicting
    itself.
    """
    cur = conn.execute(
        "DELETE FROM chat_messages WHERE session_id = ? AND id >= ?", (session_id, message_id)
    )
    return cur.rowcount


def save_message(
    conn: sqlite3.Connection,
    session_id: int,
    role: str,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, tool_calls, tool_call_id, "
        "tool_name) VALUES(?,?,?,?,?,?)",
        (
            session_id,
            role,
            content,
            json.dumps(tool_calls) if tool_calls else None,
            tool_call_id,
            tool_name,
        ),
    )
    conn.execute(
        "UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,)
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------
async def run_turn(
    session_id: int,
    message: str,
    context: dict[str, Any] | None = None,
    truncate_from_id: int | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run one exchange, yielding (event, data) as it happens.

    Opens its own connections rather than taking one: this generator outlives
    the request handler that started it, and a connection closed underneath it
    would fail mid-turn.
    """
    with db.session() as conn:
        settings = db.get_settings(conn)
        provider = settings.get("assistant_provider", "")
        model = settings.get("assistant_model", "")
        max_rounds = int(settings.get("assistant_max_rounds") or DEFAULT_MAX_ROUNDS)
        thinking = settings.get("assistant_thinking", "off")
        route = settings.get("assistant_route", "")

    if not provider or not model:
        yield "error", {"message": "Choose a provider and model under Settings → AI."}
        return
    spec = providers.PROVIDERS.get(provider)
    if spec is None:
        yield "error", {"message": f"Unknown provider '{provider}'."}
        return

    with db.session() as conn:
        # An edited message replaces the old one and everything it led to.
        if truncate_from_id is not None:
            truncate_from(conn, session_id, truncate_from_id)
        api_key = db.get_setting(conn, spec.setting)
        if not api_key:
            yield "error", {"message": f"No API key stored for {spec.label}."}
            return
        user_message_id = save_message(conn, session_id, "user", message)
        # Name the thread after its opening question, so the tab is
        # recognisable without the user having to title it.
        conn.execute(
            "UPDATE chat_sessions SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
            (message.strip()[:60], session_id),
        )
        history = load_history(conn, session_id)

    # Skills are advertised, not spelled out: one line each here, and the full
    # instructions only when the assistant decides one applies.
    system = SYSTEM_PROMPT
    catalogue = skills.index_prompt()
    if catalogue:
        system += f"\n\n{catalogue}"
    block = _context_block(context)
    if block:
        system += f"\n\nWhat the user is looking at right now:\n{block}"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history]
    started = time.monotonic()
    # The id lets the panel offer to edit this message later, which means
    # truncating the thread back to it.
    yield "start", {
        "provider": provider, "model": model, "thinking": thinking,
        "user_message_id": user_message_id,
    }

    async with make_client() as client:
        for round_no in range(max_rounds):
            if time.monotonic() - started > TURN_TIMEOUT_S:
                yield "error", {"message": "This turn took too long and was stopped."}
                return

            try:
                reply = await providers.chat(
                    client, provider, model, api_key, messages, tools.TOOL_SCHEMAS,
                    thinking=thinking, route=route,
                )
            except SourceError as exc:
                yield "error", {"message": str(exc)}
                return

            text = reply.get("content") or ""
            calls = reply.get("tool_calls") or []

            if not calls:
                with db.session() as conn:
                    save_message(conn, session_id, "assistant", text)
                yield "message", {"text": text, "rounds": round_no + 1}
                yield "done", {"rounds": round_no + 1, "usage": reply.get("usage") or {}}
                return

            if text:
                yield "text", {"text": text}
            messages.append(
                {"role": "assistant", "content": text, "tool_calls": calls}
            )
            with db.session() as conn:
                save_message(conn, session_id, "assistant", text, tool_calls=calls)

            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}

                yield "tool_call", {"id": call.get("id"), "name": name, "args": args}

                with db.session() as conn:
                    result = await tools.dispatch(conn, client, name, args)

                yield "tool_result", {
                    "id": call.get("id"),
                    "name": name,
                    "summary": tools.summarise(name, result),
                    "is_write": name in tools.WRITE_TOOLS,
                    "error": result.get("error"),
                    # Enough of the answer to check what the assistant saw.
                    "preview": tools.preview(result),
                }
                # Moving the reader is a UI action, not data, so it reaches the
                # browser as its own event rather than being buried in a result.
                if name in ("show_in_pdf", "add_highlight") and result.get("ok"):
                    # Guarded: a tool returning something other than a list here
                    # would otherwise reach the browser and be iterated.
                    rects = result.get("rects")
                    yield "reveal", {
                        "paper_id": args.get("paper_id"),
                        "page": result.get("page"),
                        "rects": rects if isinstance(rects, list) else [],
                        "quote": result.get("quote") or args.get("quote"),
                    }

                payload = json.dumps(result, default=str)[:12000]
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": payload}
                )
                with db.session() as conn:
                    save_message(
                        conn, session_id, "tool", payload,
                        tool_call_id=call.get("id"), tool_name=name,
                    )

        yield "error", {
            "message": (
                f"Stopped after {max_rounds} rounds of tool use without reaching an answer."
            )
        }
