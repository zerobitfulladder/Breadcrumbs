"""Author portraits stored in the library folder.

A portrait can arrive three ways: a file you upload, a URL you paste, or one
the assistant finds. All three end up as a file under `authors/`, so the image
keeps working when the original page moves or the host blocks hotlinking —
which is the usual fate of a remote URL held only as a link.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx

from . import db

MAX_BYTES = 12 * 1024 * 1024

# Magic numbers, because a Content-Type header is a claim, not evidence.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),          # checked further below
)


def sniff(data: bytes) -> str | None:
    """The image format, from the bytes themselves."""
    for signature, ext in _SIGNATURES:
        if data.startswith(signature):
            if ext == "webp":
                return "webp" if data[8:12] == b"WEBP" else None
            return ext
    return None


def photo_dir() -> Path:
    path = db.library_root() / "authors"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_path(conn: sqlite3.Connection, author_id: int) -> Path | None:
    row = conn.execute(
        "SELECT custom_image FROM author_profiles WHERE author_id = ?", (author_id,)
    ).fetchone()
    if not row or not row["custom_image"]:
        return None
    path = db.library_root() / row["custom_image"]
    return path if path.exists() else None


def save_bytes(
    conn: sqlite3.Connection, author_id: int, data: bytes, source: str
) -> dict[str, Any]:
    """Write image bytes as this author's portrait, replacing any earlier one."""
    if len(data) > MAX_BYTES:
        return {"error": f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB."}
    ext = sniff(data)
    if ext is None:
        return {"error": "That file is not a JPEG, PNG, GIF or WebP image."}

    # One file per author: drop other extensions so a replacement cannot leave
    # the previous format behind to be served instead.
    for old in photo_dir().glob(f"{author_id}.*"):
        old.unlink(missing_ok=True)

    rel = f"authors/{author_id}.{ext}"
    (db.library_root() / rel).write_bytes(data)

    conn.execute(
        "INSERT INTO author_profiles(author_id, status, custom_image, custom_image_source) "
        "VALUES(?, 'none', ?, ?) "
        "ON CONFLICT(author_id) DO UPDATE SET custom_image = excluded.custom_image, "
        "  custom_image_source = excluded.custom_image_source",
        (author_id, rel, source),
    )
    return {"ok": True, "path": rel, "format": ext, "bytes": len(data)}


async def save_from_url(
    conn: sqlite3.Connection, client: httpx.AsyncClient, author_id: int, url: str
) -> dict[str, Any]:
    """Fetch a URL and cache it as the portrait."""
    try:
        resp = await client.get(url, timeout=45.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"error": f"Could not fetch that image: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"That URL returned HTTP {resp.status_code}."}
    return save_bytes(conn, author_id, resp.content, source=url)


def clear(conn: sqlite3.Connection, author_id: int) -> dict[str, Any]:
    for old in photo_dir().glob(f"{author_id}.*"):
        old.unlink(missing_ok=True)
    conn.execute(
        "UPDATE author_profiles SET custom_image = NULL, custom_image_source = NULL "
        "WHERE author_id = ?",
        (author_id,),
    )
    return {"ok": True}
