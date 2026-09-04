"""Skills: capabilities the assistant loads only when it needs them.

Only a name and a one-line description sit in the system prompt. The full
instructions are fetched with `read_skill` when the assistant decides a skill
applies. That keeps the standing prompt short — the SQL skill alone would
otherwise add the whole schema to every single message — while still letting
the assistant discover what it can do.

Skills are Markdown files with a small frontmatter block, so they can be edited
without touching code. A body may contain `{{SCHEMA}}`, which is replaced with
the live database schema at read time; a schema pasted into the file would
drift the first time a column changed.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).with_name("skills")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def _parse(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return None

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")

    name = meta.get("name") or path.stem
    description = meta.get("description", "")
    return Skill(name=name, description=description, body=text[match.end():].strip())


@lru_cache(maxsize=1)
def _load() -> dict[str, Skill]:
    if not SKILLS_DIR.is_dir():
        return {}
    found: dict[str, Skill] = {}
    for path in sorted(SKILLS_DIR.glob("*.md")):
        skill = _parse(path)
        if skill:
            found[skill.name] = skill
    return found


def reload() -> None:
    """Forget the cache, so an edited skill takes effect without a restart."""
    _load.cache_clear()


def index() -> list[Skill]:
    return list(_load().values())


def index_prompt() -> str:
    """The one-line-per-skill listing that goes in the system prompt."""
    skills = index()
    if not skills:
        return ""
    lines = "\n".join(f"- `{s.name}`: {s.description}" for s in skills)
    return (
        "Skills you can load with `read_skill` when one applies. Read a skill before "
        "using the capability it describes; do not guess at it.\n" + lines
    )


def schema_summary(conn: sqlite3.Connection) -> str:
    """The live schema, so a skill can never describe a stale one."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' ORDER BY name"
    ).fetchall()

    parts = []
    for row in rows:
        columns = conn.execute(f"PRAGMA table_info({row['name']})").fetchall()
        cols = ", ".join(
            f"{c['name']} {c['type'] or 'ANY'}" + (" PK" if c["pk"] else "") for c in columns
        )
        parts.append(f"{row['name']}({cols})")
    return "\n".join(parts)


def read(name: str, conn: sqlite3.Connection | None = None) -> Skill | None:
    skill = _load().get(name)
    if skill is None:
        return None
    body = skill.body
    if "{{SCHEMA}}" in body and conn is not None:
        body = body.replace("{{SCHEMA}}", schema_summary(conn))
    return Skill(skill.name, skill.description, body)
