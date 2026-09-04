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
    """Root folder holding the database, PDFs and note files."""
    env = os.environ.get("BREADCRUMBS_HOME")
    root = Path(env).expanduser() if env else Path.home() / "Breadcrumbs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pdfs").mkdir(exist_ok=True)
    (root / "notes").mkdir(exist_ok=True)
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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not
# add a column to a table that already exists, so they are applied separately.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # REAL, not INTEGER: a hand-placed card keeps a fractional position.
    # SQLite stores either in the same column, so the existing values carry
    # over untouched.
    ("papers", "lane", "REAL"),
    # Starred by hand. On both papers and authors because the two lists are
    # browsed the same way and "the ones I care about" means the same thing in
    # each. NOT NULL with a default, so existing rows come back as not starred
    # rather than as unknown.
    ("papers", "favorite", "INTEGER NOT NULL DEFAULT 0"),
    ("authors", "favorite", "INTEGER NOT NULL DEFAULT 0"),
    # `lat`/`lon` already existed on institutions; only the lookup marker and
    # city are new. Filled from OpenAlex on demand rather than at import,
    # because most institutions are never looked at on a map.
    ("institutions", "city", "TEXT"),
    ("institutions", "geo_checked", "TEXT"),
    # The biography itself, kept apart from where it came from. Wikipedia is
    # one source among several, not the field's definition.
    ("author_profiles", "bio", "TEXT"),
    ("author_profiles", "bio_source", "TEXT"),
    ("author_profiles", "bio_updated_at", "TEXT"),
    # A portrait you supplied or the assistant found, cached in the library
    # folder so it survives the original page moving or blocking hotlinks.
    ("author_profiles", "custom_image", "TEXT"),
    ("author_profiles", "custom_image_source", "TEXT"),
    # Wikidata claims about this person, as JSON. Discrete facts that can be
    # shown and checked one by one, unlike a prose paragraph.
    ("author_profiles", "facts", "TEXT"),
    # Page size in points, stored beside the fractional rects so an export can
    # convert a highlight back to absolute PDF units.
    ("highlights", "page_width", "REAL"),
    ("highlights", "page_height", "REAL"),
    ("highlights", "source", "TEXT"),
)

# Tables from earlier designs that are no longer part of the app.
_DROPPED_TABLES: tuple[str, ...] = ("ai_tasks",)

# Columns added by mistake and superseded. Dropped so the table has one
# obvious place for each fact.
_DROPPED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("institutions", "latitude"),
    ("institutions", "longitude"),
)


def _apply_migrations(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, column, decl in _ADDED_COLUMNS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    for table, column in _DROPPED_COLUMNS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            applied.append(f"-{table}.{column}")
    for table in _DROPPED_TABLES:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            conn.execute(f"DROP TABLE {table}")
            applied.append(f"-{table}")
    return applied


def init_db() -> None:
    """Create the schema if absent, then bring an existing one up to date."""
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        applied = _apply_migrations(conn)
        conn.commit()
        if applied:
            print(f"breadcrumbs: added column(s) {', '.join(applied)}")
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
    "ai_gemini_key",
    "ai_deepseek_key",
    "search_api_key",
}


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
