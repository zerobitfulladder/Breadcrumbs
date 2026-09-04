"""Crossref adapter: authoritative publisher metadata and licence terms.

Used as a fallback for venue/publisher/licence when OpenAlex is thin. Its
reference lists are frequently unstructured strings, so they are not used for
link building.
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import ids
from .markup import clean_title
from .http import get_json

BASE = "https://api.crossref.org"


def parse_work(m: dict[str, Any]) -> dict[str, Any]:
    issued = ((m.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued else None
    month = issued[1] if len(issued) > 1 else None

    authors = []
    for pos, a in enumerate(m.get("author") or []):
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if not name:
            continue
        authors.append(
            {
                "name": name,
                "orcid": ids.norm_orcid(a.get("ORCID")),
                "position": pos,
                "is_corresponding": False,
                "institutions": [
                    {"name": i.get("name"), "ror": None, "openalex_id": None,
                     "country_code": None, "type": None}
                    for i in (a.get("affiliation") or []) if i.get("name")
                ],
            }
        )

    titles = m.get("title") or []
    containers = m.get("container-title") or []
    licences = [l.get("URL") for l in (m.get("license") or []) if l.get("URL")]

    return {
        "title": clean_title(titles[0]) if titles else None,
        "year": year,
        "month": month,
        "venue": containers[0] if containers else None,
        "publisher": m.get("publisher"),
        "volume": m.get("volume"),
        "pages": m.get("page"),
        "abstract": m.get("abstract"),
        "doi": ids.norm_doi(m.get("DOI")),
        "url": m.get("URL"),
        "reference_count": m.get("reference-count"),
        "license": licences[0] if licences else None,
        "type": m.get("type"),
        "authors": authors,
    }


async def fetch_by_doi(
    client: httpx.AsyncClient, doi: str, email: str
) -> dict[str, Any] | None:
    headers = {"User-Agent": f"Breadcrumbs/0.1 (mailto:{email})"} if email else None
    data = await get_json(client, f"{BASE}/works/{doi}", source="crossref", headers=headers)
    msg = (data or {}).get("message")
    return parse_work(msg) if msg else None
