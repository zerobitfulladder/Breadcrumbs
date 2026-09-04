"""Unpaywall adapter: resolves a DOI to a legally hosted open-access PDF."""
from __future__ import annotations

from typing import Any

import httpx

from .http import get_json

BASE = "https://api.unpaywall.org/v2"


async def fetch_oa(
    client: httpx.AsyncClient, doi: str, email: str
) -> dict[str, Any] | None:
    if not email:
        return None  # Unpaywall requires a contact address
    data = await get_json(client, f"{BASE}/{doi}", source="unpaywall", params={"email": email})
    if not data:
        return None
    best = data.get("best_oa_location") or {}
    # Every legal copy, not only the one Unpaywall ranks first. The best
    # location is often a publisher landing page that serves HTML; a repository
    # further down the list serves the actual file.
    locations = [
        {
            "pdf_url": loc.get("url_for_pdf"),
            "landing_page_url": loc.get("url"),
            "host": loc.get("host_type"),        # publisher | repository
            "version": loc.get("version"),
            "license": loc.get("license"),
        }
        for loc in (data.get("oa_locations") or [])
    ]
    return {
        "is_oa": bool(data.get("is_oa")),
        "oa_status": data.get("oa_status"),
        "oa_pdf_url": best.get("url_for_pdf") or best.get("url"),
        "license": best.get("license"),
        "oa_locations": locations,
    }
