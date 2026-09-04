"""Semantic Scholar adapter.

Now a narrow role. Autocomplete is the main reason it is here: that endpoint is
consistently fast and tolerates bursts, which OpenAlex's equivalent does not.
Beyond that it supplies the influential-citation count and acts as a fallback
when OpenAlex cannot resolve an identifier.

Its citation contexts and intent labels are deliberately not used. Typing a
link is a judgement the user makes, not something inferred from a third party.
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import ids
from .markup import clean_title
from .http import get_json

BASE = "https://api.semanticscholar.org/graph/v1"

PAPER_FIELDS = ",".join(
    [
        "paperId", "title", "year", "publicationDate", "abstract", "venue",
        "publicationVenue", "externalIds", "openAccessPdf",
        "fieldsOfStudy", "citationCount", "referenceCount",
        "influentialCitationCount", "publicationTypes", "authors",
    ]
)
# NOTE: on the citations/references endpoints the nested paper's fields are
# named bare (`authors`, not `authors.name`); `authors.name` is rejected with
# "Unrecognized or unsupported fields".
EDGE_FIELDS = "title,year,externalIds,citationCount,authors,contexts,intents,isInfluential"


def _headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key} if api_key else {}


def parse_paper(d: dict[str, Any]) -> dict[str, Any]:
    ext = d.get("externalIds") or {}
    oa_pdf = d.get("openAccessPdf") or {}
    venue_obj = d.get("publicationVenue") or {}
    date = d.get("publicationDate") or ""
    month = int(date[5:7]) if len(date) >= 7 and date[5:7].isdigit() else None

    authors = []
    for pos, a in enumerate(d.get("authors") or []):
        authors.append(
            {
                "name": a.get("name"),
                "s2_author_id": ids.norm_s2(a.get("authorId")),
                "position": pos,
                "institutions": [],
            }
        )

    return {
        "title": clean_title(d.get("title")),
        "year": d.get("year"),
        "month": month,
        "venue": venue_obj.get("name") or d.get("venue"),
        "abstract": d.get("abstract"),
        "doi": ids.norm_doi(ext.get("DOI")),
        "arxiv_id": ids.norm_arxiv(ext.get("ArXiv")),
        "s2_id": ids.norm_s2(d.get("paperId")),
        "pmid": str(ext.get("PubMed")) if ext.get("PubMed") else None,
        "citation_count": d.get("citationCount"),
        "reference_count": d.get("referenceCount"),
        "influential_citation_count": d.get("influentialCitationCount"),
        # an empty url with status CLOSED means "no PDF", not "PDF at ''"
        "oa_pdf_url": (oa_pdf.get("url") or None),
        "oa_status": (oa_pdf.get("status") or "").lower() or None,
        "fields_of_study": d.get("fieldsOfStudy") or [],
        "authors": authors,
    }


def _parse_edge(entry: dict[str, Any], side: str) -> dict[str, Any] | None:
    """`side` is 'citingPaper' or 'citedPaper'."""
    p = entry.get(side) or {}
    if not p:
        return None
    ext = p.get("externalIds") or {}
    names = [a.get("name") or "" for a in (p.get("authors") or [])[:5]]
    contexts = entry.get("contexts") or []
    intents = entry.get("intents") or []
    return {
        "doi": ids.norm_doi(ext.get("DOI")),
        "arxiv_id": ids.norm_arxiv(ext.get("ArXiv")),
        "s2_id": ids.norm_s2(p.get("paperId")),
        "openalex_id": None,
        "title": clean_title(p.get("title")),
        "year": p.get("year"),
        "authors_blob": ", ".join(n for n in names if n) or None,
        "citation_count": p.get("citationCount"),
        "context": contexts[0] if contexts else None,
        "intent": intents[0] if intents else None,
        "is_influential": 1 if entry.get("isInfluential") else 0,
    }


async def fetch_paper(
    client: httpx.AsyncClient, ident: str, api_key: str = ""
) -> dict[str, Any] | None:
    """`ident` may be a raw paperId, 'DOI:10.x/y', or 'arXiv:1234.5678'."""
    data = await get_json(
        client, f"{BASE}/paper/{ident}",
        source="semantic_scholar", params={"fields": PAPER_FIELDS},
        headers=_headers(api_key),
    )
    return parse_paper(data) if data else None


async def _fetch_edges(
    client: httpx.AsyncClient, ident: str, kind: str, side: str, api_key: str, limit: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < limit:
        page = min(1000 if api_key else 100, limit - len(out))
        data = await get_json(
            client, f"{BASE}/paper/{ident}/{kind}",
            source="semantic_scholar",
            params={"fields": EDGE_FIELDS, "limit": page, "offset": offset},
            headers=_headers(api_key),
        )
        rows = (data or {}).get("data") or []
        if not rows:
            break
        for entry in rows:
            rec = _parse_edge(entry, side)
            if rec:
                out.append(rec)
        if "next" not in (data or {}):
            break
        offset = data["next"]
    return out


async def fetch_citations(
    client: httpx.AsyncClient, ident: str, api_key: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    """Papers that cite `ident`, carrying context and intent."""
    return await _fetch_edges(client, ident, "citations", "citingPaper", api_key, limit)


async def fetch_references(
    client: httpx.AsyncClient, ident: str, api_key: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    """Papers that `ident` cites, carrying context and intent."""
    return await _fetch_edges(client, ident, "references", "citedPaper", api_key, limit)


async def autocomplete(
    client: httpx.AsyncClient, query: str, api_key: str = ""
) -> list[dict[str, Any]]:
    """Typeahead suggestions. Returns at most 10 matches, a fixed server cap.

    This endpoint is far lighter than /paper/search and tolerated a burst of
    ten back-to-back calls unauthenticated without a 429, which is what makes
    it usable while the user types. It carries only id, title and a combined
    "authors, year" string, so the UI must not expect venue or counts here.
    """
    data = await get_json(
        client, f"{BASE}/paper/autocomplete",
        source="semantic_scholar", params={"query": query}, headers=_headers(api_key),
    )
    out = []
    for m in (data or {}).get("matches") or []:
        out.append(
            {
                "s2_id": ids.norm_s2(m.get("id")),
                "title": clean_title(m.get("title")),
                "authors_year": m.get("authorsYear"),
            }
        )
    return out


async def search(
    client: httpx.AsyncClient, query: str, api_key: str = "", limit: int = 10
) -> list[dict[str, Any]]:
    data = await get_json(
        client, f"{BASE}/paper/search",
        source="semantic_scholar",
        params={
            "query": query, "limit": limit,
            "fields": "paperId,title,year,venue,externalIds,citationCount,authors,abstract",
        },
        headers=_headers(api_key),
    )
    return [parse_paper(d) for d in (data or {}).get("data", [])]
