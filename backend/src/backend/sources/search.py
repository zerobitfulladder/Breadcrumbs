"""Web search, kept separate from the model provider.

Deliberately its own service rather than a provider-native tool. Search
availability differs sharply between providers (Gemini grounds on Google,
DeepSeek has none at all), so binding tasks to provider search would make half
the task-to-model combinations unusable. With search here, any task runs on
any model.
"""
from __future__ import annotations

from typing import Any

import httpx

from .http import SourceError, get_json

BRAVE = "https://api.search.brave.com/res/v1/web/search"
TAVILY = "https://api.tavily.com/search"


async def _brave(
    client: httpx.AsyncClient, query: str, api_key: str, count: int
) -> list[dict[str, Any]]:
    data = await get_json(
        client, BRAVE, source="search",
        params={"q": query, "count": count},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    results = ((data or {}).get("web") or {}).get("results") or []
    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "snippet": r.get("description"),
        }
        for r in results
    ]


async def _tavily(
    client: httpx.AsyncClient, query: str, api_key: str, count: int
) -> list[dict[str, Any]]:
    try:
        resp = await client.post(
            TAVILY,
            json={"api_key": api_key, "query": query, "max_results": count},
            timeout=45.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise SourceError("search", f"tavily: {exc}") from exc
    return [
        {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
        for r in data.get("results", [])
    ]


async def search(
    client: httpx.AsyncClient, query: str, provider: str, api_key: str, count: int = 8
) -> list[dict[str, Any]]:
    if not api_key:
        raise SourceError("search", "no search API key configured")
    if provider == "tavily":
        return await _tavily(client, query, api_key, count)
    return await _brave(client, query, api_key, count)
