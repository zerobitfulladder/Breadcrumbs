"""ORCID adapter: a researcher's own record.

The most authoritative source there is, because the person maintains it — and
the least reliably populated, for the same reason. Most records carry
employment but leave the biography blank, so this is treated as one
contributor to a profile rather than the profile itself.

The public API needs no key, only an Accept header.
"""
from __future__ import annotations

from typing import Any

import httpx

from .http import get_json

BASE = "https://pub.orcid.org/v3.0"
_HEADERS = {"Accept": "application/json"}


def _year(node: dict[str, Any] | None) -> int | None:
    if not node:
        return None
    value = (node.get("year") or {}).get("value")
    return int(value) if value else None


async def fetch_person(
    client: httpx.AsyncClient, orcid: str
) -> dict[str, Any] | None:
    """Name, self-written biography, keywords and personal links."""
    data = await get_json(client, f"{BASE}/{orcid}/person", source="orcid", headers=_HEADERS)
    if not data:
        return None

    biography = ((data.get("biography") or {}).get("content") or "").strip()
    urls = [
        {"name": u.get("url-name"), "url": (u.get("url") or {}).get("value")}
        for u in (data.get("researcher-urls") or {}).get("researcher-url", [])
        if (u.get("url") or {}).get("value")
    ]
    keywords = [
        k.get("content")
        for k in (data.get("keywords") or {}).get("keyword", [])
        if k.get("content")
    ]
    return {"biography": biography or None, "urls": urls, "keywords": keywords}


async def fetch_employments(
    client: httpx.AsyncClient, orcid: str
) -> list[dict[str, Any]]:
    """Where they have worked, most recent first."""
    data = await get_json(
        client, f"{BASE}/{orcid}/employments", source="orcid", headers=_HEADERS
    )
    out: list[dict[str, Any]] = []
    for group in (data or {}).get("affiliation-group", []):
        for summary in group.get("summaries", []):
            emp = summary.get("employment-summary") or {}
            organisation = emp.get("organization") or {}
            address = organisation.get("address") or {}
            out.append(
                {
                    "organisation": organisation.get("name"),
                    "role": emp.get("role-title"),
                    "department": emp.get("department-name"),
                    "city": address.get("city"),
                    "country": address.get("country"),
                    "start_year": _year(emp.get("start-date")),
                    "end_year": _year(emp.get("end-date")),
                }
            )
    # Current posts first, then most recent.
    out.sort(key=lambda e: (e["end_year"] is not None, -(e["start_year"] or 0)))
    return out
