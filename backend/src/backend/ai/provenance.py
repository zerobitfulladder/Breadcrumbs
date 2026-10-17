"""The record of one assistant turn: what went in, and what the model read.

A language model cannot be re-run to the same answer. Even at temperature zero
providers batch requests and update models behind a stable name, so "run it
again" is not reproducibility here. What can be kept is the full set of inputs,
so that anyone looking at an answer later can see exactly what produced it:

  * the model the provider says served each request, which is not always the
    one asked for (an alias moves; OpenRouter routes to an upstream)
  * the system prompt as sent, including the page the user was reading
  * a hash of the tool definitions and the app's commit, naming the code
  * the SHA-256 of every PDF the assistant read, so a re-downloaded file with
    different page numbers is distinguishable from the one it quoted
  * token counts, rounds, and how its citations checked out

Observations made during the turn also live here, because some decisions must
rest on what the server saw rather than on what the model reports. A homepage
is marked verified only if this turn fetched it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import pdftext


@lru_cache(maxsize=1)
def app_version() -> str | None:
    """The checkout's commit, with "+dirty" when the app's code has uncommitted edits.

    Only backend/ and frontend/ count. A regenerated screenshot or an edited
    README changes nothing the server does, and marking every answer dirty
    for it would make the mark meaningless.
    """
    root = Path(__file__).resolve().parents[4]
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no",
             "--", "backend", "frontend"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{commit}+dirty" if dirty else commit


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def tools_sha256() -> str:
    from .tools import TOOL_SCHEMAS

    return sha256_text(json.dumps(TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False))


def _normal_url(url: str) -> str:
    """A URL in the form two spellings of the same page share."""
    url = (url or "").strip().lower()
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    if url.startswith("www."):
        url = url[4:]
    return url.split("#", 1)[0].rstrip("/")


@dataclass
class Turn:
    session_id: int
    provider: str
    model: str
    route: str = ""
    thinking: str = ""
    system_prompt: str = ""
    row_id: int | None = None
    rounds: int = 0
    responses: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, float] = field(default_factory=dict)
    documents: dict[int, dict[str, Any]] = field(default_factory=dict)
    fetched: dict[str, str] = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    absences: list[dict[str, Any]] = field(default_factory=list)
    repair: dict[str, Any] | None = None

    # --- observations -------------------------------------------------------
    def note_response(self, reply: dict[str, Any]) -> None:
        self.rounds += 1
        usage = reply.get("usage") or {}
        self.responses.append(
            {"round": self.rounds, **(reply.get("served") or {}),
             "sent": reply.get("sent") or {}, "usage": usage}
        )
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.usage[key] = self.usage.get(key, 0) + value

    def note_document(self, conn: sqlite3.Connection, paper_id: Any) -> None:
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return
        if paper_id in self.documents:
            return
        path = pdftext.pdf_path(conn, paper_id)
        if path is None:
            return
        row = conn.execute("SELECT title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        self.documents[paper_id] = {
            "paper_id": paper_id,
            "title": row["title"] if row else None,
            "sha256": pdftext.file_sha256(path),
            "bytes": path.stat().st_size,
        }

    def note_fetch(self, requested: str, final: str | None, text: str) -> None:
        for url in (requested, final):
            if url:
                self.fetched[_normal_url(url)] = text

    def fetched_text(self, url: str) -> str | None:
        return self.fetched.get(_normal_url(url))

    # --- storage ------------------------------------------------------------
    def open(self, conn: sqlite3.Connection, user_message_id: int) -> None:
        cur = conn.execute(
            "INSERT INTO chat_turns(session_id, user_message_id, provider, model, route, "
            "thinking, app_version, system_prompt, system_sha256, tools_sha256) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                self.session_id, user_message_id, self.provider, self.model, self.route,
                self.thinking, app_version(), self.system_prompt,
                sha256_text(self.system_prompt), tools_sha256(),
            ),
        )
        self.row_id = int(cur.lastrowid)

    def close(
        self,
        conn: sqlite3.Connection,
        status: str,
        answer_message_id: int | None = None,
        citations: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self.row_id is None:
            return
        conn.execute(
            "UPDATE chat_turns SET status = ?, answer_message_id = ?, responses = ?, "
            "tools_used = ?, absences = ?, documents = ?, usage = ?, rounds = ?, citations = ?, "
            "repair = ?, error = ?, finished_at = datetime('now') WHERE id = ?",
            (
                status, answer_message_id, json.dumps(self.responses), json.dumps(self.tools_used),
                json.dumps(self.absences),
                json.dumps(list(self.documents.values())), json.dumps(self.usage),
                self.rounds, json.dumps(citations) if citations is not None else None,
                json.dumps(self.repair) if self.repair else None, error, self.row_id,
            ),
        )


def load(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """A stored turn with its JSON columns parsed, for the export."""
    out = dict(row)
    for key in ("responses", "tools_used", "absences", "documents", "usage", "citations", "repair"):
        if out.get(key):
            try:
                out[key] = json.loads(out[key])
            except ValueError:
                pass
    return out


def public(conn: sqlite3.Connection, turn_id: int | None) -> dict[str, Any] | None:
    """A turn's record for the panel: everything but the prompt text itself.

    The prompt carries a whole page of the paper, which the panel has no use
    for; its hash identifies it, and the export includes it in full.
    """
    if turn_id is None:
        return None
    row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
    if row is None:
        return None
    out = load(row)
    out.pop("system_prompt", None)
    return out
