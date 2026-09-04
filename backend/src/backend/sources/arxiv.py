"""arXiv: direct PDF URLs, and a title search for finding the author's copy.

The abs/pdf URLs are stable, so building one needs no API call. The search does
call the API, and exists because a paper's stored DOI often points at a
paywalled journal version while the author's own manuscript sits on arXiv under
no identifier the record carries.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .http import SourceError, get_text


def pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}"


def abs_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


async def search_by_title(
    client: httpx.AsyncClient, title: str, limit: int = 5
) -> list[dict[str, Any]]:
    """arXiv entries whose title matches, for papers with no stored arXiv id.

    Plenty of the machine-learning literature is on arXiv under a DOI that
    points at a paywalled journal version, so the record carries no arXiv id at
    all. Searching by title is the only way to find the author's own copy.
    """
    import xml.etree.ElementTree as ET

    cleaned = re.sub(r'[^\w\s-]', " ", title or "").strip()
    if not cleaned:
        return []

    body = await get_text(
        client,
        "https://export.arxiv.org/api/query",
        source="arxiv",
        params={"search_query": f'ti:"{cleaned}"', "max_results": limit},
    )
    if not body:
        return []

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError("arxiv", f"bad Atom response: {exc}") from exc

    out = []
    for entry in root.findall("a:entry", ns):
        ident = (entry.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        if not ident:
            continue
        out.append(
            {
                "arxiv_id": ident,
                "title": " ".join((entry.findtext("a:title", "", ns) or "").split()),
                "authors": [
                    (a.findtext("a:name", "", ns) or "")
                    for a in entry.findall("a:author", ns)
                ],
                "pdf_url": pdf_url(ident),
            }
        )
    return out
