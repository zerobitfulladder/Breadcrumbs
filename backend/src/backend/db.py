"""SQLite access layer.

One database file per library. Connections are per-request (FastAPI dependency)
because SQLite objects are not safe to share across threads.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def library_root() -> Path:
    """Root folder holding the database and the PDFs."""
    env = os.environ.get("BREADCRUMBS_HOME")
    root = Path(env).expanduser() if env else Path.home() / "Breadcrumbs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pdfs").mkdir(exist_ok=True)
    return root


def db_path() -> Path:
    return library_root() / "library.db"


def connect() -> sqlite3.Connection:
    # check_same_thread=False is required because FastAPI resolves sync
    # dependencies on a worker thread while async endpoints run on the event
    # loop thread, so one request legitimately touches its connection from
    # both. Safe here: every request gets its own connection and uses it
    # sequentially, so no connection is ever shared between requests.
    conn = sqlite3.connect(db_path(), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


#: What papers.status accepts: the CHECK constraint in schema.sql, for code
#: that has to name the values before the database sees them.
STATUSES = ("unread", "queued", "reading", "skimmed", "read", "archived")


def init_db() -> None:
    """Create the library from schema.sql, if it does not exist yet.

    The schema is never altered in place. Every statement in it is IF NOT
    EXISTS or OR IGNORE, so running it against a library it built changes
    nothing.
    """
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    with session() as conn:
        yield conn


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
SECRET_KEYS = {
    "openalex_key",
    "semantic_scholar_key",
    "ai_openrouter_key",
    "ai_anthropic_key",
    "ai_openai_key",
    "ai_gemini_key",
    "ai_deepseek_key",
    "search_api_key",
}

#: Tables the assistant's SQL may not read, because they hold the keys above.
#: Whatever it reads goes to the model provider and into the chat history, so
#: an API key read here would leave the machine.
PRIVATE_TABLES = frozenset({"settings"})


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: (r["value"] or "") for r in conn.execute("SELECT key, value FROM settings")}


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return (row["value"] if row and row["value"] is not None else default) or default


def set_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, "" if value is None else str(value)),
        )


def redact(settings: dict[str, str]) -> dict[str, Any]:
    """Never send a stored key back to the browser; report presence only."""
    out: dict[str, Any] = {}
    for k, v in settings.items():
        if k in SECRET_KEYS:
            out[k] = ""
            out[f"{k}_set"] = bool(v)
        else:
            out[k] = v
    return out


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def json_or_none(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None
