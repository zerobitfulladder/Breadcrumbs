"""OpenAlex adapter.

The structural spine of a record: identity, people, institutions with country
codes, topics, and the reference list. Free, no key, 100k/day with an email.
"""
from __future__ import annotations

from typing import Any

import re

import httpx

from .. import ids
from .markup import clean_title
from .http import get_json

norm_openalex = ids.norm_openalex

# OpenAlex filter values are parsed, not taken literally. "," separates
# filters, "|" separates OR values, ":" splits field from value, and "*"/"?"
# are wildcards the stemmed title.search field rejects outright. The remaining
# characters here are Lucene syntax that can produce a 400 in combination.
# A footnote marker glued to a title — "Boltzmann Machines**The research…" —
# is enough to break the request, so the set is deliberately broad.
_FILTER_UNSAFE = re.compile(r"""[,|:*?!(){}\[\]^~\\/"+]+""")
# Some records title a reprint with the whole original citation, e.g.
#   (1982) Teuvo Kohonen, "Self-organized formation of ...," Biol. Cyb. 43: 59-69
# The quoted span is the actual title.
_QUOTED_TITLE = re.compile(r"[\"\u201c]([^\"\u201d]{12,})[\"\u201d]")


def clean_title_for_search(title: str, max_words: int = 18) -> str:
    """Make a title safe to put in a title.search filter.

    Also truncated: a "title" that is really a title plus a footnote runs to
    hundreds of words, and searching all of them finds nothing. The opening
    words are what identify the work.
    """
    quoted = _QUOTED_TITLE.search(title or "")
    if quoted:
        title = quoted.group(1)
    cleaned = re.sub(r"\s+", " ", _FILTER_UNSAFE.sub(" ", title or "")).strip()
    words = cleaned.split()
    return " ".join(words[:max_words])

BASE = "https://api.openalex.org"


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the text."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        positions.extend((i, word) for i in idxs)
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions) or None


def _institutions(inst_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for inst in inst_list or []:
        out.append(
            {
                "name": inst.get("display_name"),
                "ror": (inst.get("ror") or "").rstrip("/").rsplit("/", 1)[-1] or None,
                "openalex_id": ids.norm_openalex(inst.get("id")),
                "country_code": inst.get("country_code"),
                "type": inst.get("type"),
            }
        )
    return out


def _locations(w: dict[str, Any]) -> list[dict[str, Any]]:
    """Every copy OpenAlex knows about, open or not.

    A paywalled article often has a legal author manuscript in an institutional
    or subject repository, and that copy only shows up here.
    """
    out = []
    for loc in w.get("locations") or []:
        source = loc.get("source") or {}
        out.append(
            {
                "is_oa": bool(loc.get("is_oa")),
                "kind": source.get("type"),          # journal | repository | ...
                "name": source.get("display_name"),
                "pdf_url": loc.get("pdf_url"),
                "landing_page_url": loc.get("landing_page_url"),
                "license": loc.get("license"),
                "version": loc.get("version"),       # submitted/accepted/published
            }
        )
    return out


def parse_work(w: dict[str, Any]) -> dict[str, Any]:
    """Map an OpenAlex work onto the internal paper record shape."""
    ext = w.get("ids") or {}
    primary = w.get("primary_location") or {}
    source = primary.get("source") or {}
    oa = w.get("open_access") or {}
    biblio = w.get("biblio") or {}

    date = w.get("publication_date") or ""
    month = None
    if len(date) >= 7 and date[5:7].isdigit():
        month = int(date[5:7])

    authors = []
    for pos, a in enumerate(w.get("authorships") or []):
        person = a.get("author") or {}
        authors.append(
            {
                "name": person.get("display_name") or a.get("raw_author_name"),
                "openalex_id": ids.norm_openalex(person.get("id")),
                "orcid": ids.norm_orcid(person.get("orcid")),
                "position": pos,
                "is_corresponding": bool(a.get("is_corresponding")),
                "institutions": _institutions(a.get("institutions") or []),
            }
        )

    topics = []
    for t in (w.get("topics") or [])[:5]:
        topics.append(
            {
                "name": t.get("display_name"),
                "openalex_id": ids.norm_openalex(t.get("id")),
                "subfield": (t.get("subfield") or {}).get("display_name"),
                "field": (t.get("field") or {}).get("display_name"),
                "domain": (t.get("domain") or {}).get("display_name"),
                "score": t.get("score"),
            }
        )

    return {
        "title": clean_title(w.get("title") or w.get("display_name")),
        "year": w.get("publication_year"),
        "month": month,
        "venue": source.get("display_name"),
        "publisher": source.get("host_organization_name"),
        "volume": biblio.get("volume"),
        "pages": "-".join(x for x in (biblio.get("first_page"), biblio.get("last_page")) if x) or None,
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "doi": ids.norm_doi(w.get("doi")),
        "openalex_id": ids.norm_openalex(w.get("id")),
        "pmid": (ext.get("pmid") or "").rsplit("/", 1)[-1] or None,
        "url": primary.get("landing_page_url") or w.get("doi"),
        "citation_count": w.get("cited_by_count"),
        "reference_count": w.get("referenced_works_count"),
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status"),
        "oa_pdf_url": oa.get("oa_url") or primary.get("pdf_url"),
        "license": primary.get("license"),
        "type": w.get("type"),
        "locations": _locations(w),
        "authors": authors,
        "topics": topics,
        # bare W-ids; resolved to full records only on demand
        "referenced_works": [ids.norm_openalex(x) for x in (w.get("referenced_works") or [])],
    }


async def fetch_by_doi(client: httpx.AsyncClient, doi: str, email: str) -> dict[str, Any] | None:
    data = await get_json(
        client, f"{BASE}/works/https://doi.org/{doi}",
        source="openalex", params={"mailto": email} if email else None,
    )
    return parse_work(data) if data else None


async def fetch_by_id(client: httpx.AsyncClient, oa_id: str, email: str) -> dict[str, Any] | None:
    data = await get_json(
        client, f"{BASE}/works/{oa_id}",
        source="openalex", params={"mailto": email} if email else None,
    )
    return parse_work(data) if data else None


async def fetch_works_batch(
    client: httpx.AsyncClient, oa_ids: list[str], email: str
) -> list[dict[str, Any]]:
    """Resolve up to 50 OpenAlex ids per call using the OR filter."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(oa_ids), 50):
        chunk = [x for x in oa_ids[i : i + 50] if x]
        if not chunk:
            continue
        params = {"filter": "openalex_id:" + "|".join(chunk), "per-page": 50}
        if email:
            params["mailto"] = email
        data = await get_json(client, f"{BASE}/works", source="openalex", params=params)
        for w in (data or {}).get("results", []):
            out.append(parse_work(w))
    return out


async def find_by_title(
    client: httpx.AsyncClient, title: str, email: str, year: int | None = None
) -> dict[str, Any] | None:
    """Best exact-ish title match, used to recover when an id lookup fails.

    Picking a typeahead suggestion gives us a Semantic Scholar id and a title.
    If Semantic Scholar is throttled the id is useless, but the title still
    resolves here, so the lookup survives.
    """
    search = clean_title_for_search(title)
    if not search:
        return None
    params: dict[str, Any] = {
        "filter": f"title.search:{search}",
        "per-page": 5,
        "sort": "relevance_score:desc",
    }
    if year:
        params["filter"] += f",publication_year:{year}"
    if email:
        params["mailto"] = email

    data = await get_json(client, f"{BASE}/works", source="openalex", params=params)
    results = (data or {}).get("results") or []
    if not results:
        return None

    wanted = "".join(ch for ch in search.lower() if ch.isalnum())
    for work in results:
        got = "".join(
            ch for ch in clean_title_for_search(work.get("title") or "").lower() if ch.isalnum()
        )
        # Require a near-exact title so a loose relevance hit is never mistaken
        # for the paper the user actually picked.
        if got and (got == wanted or got.startswith(wanted[:60]) or wanted.startswith(got[:60])):
            return parse_work(work)
    return None


async def find_versions(
    client: httpx.AsyncClient, title: str, email: str, exclude: str | None = None
) -> list[dict[str, Any]]:
    """Other records for the same work: reprints, preprints, book chapters.

    A paper is often indexed several times over. Kohonen's self-organising map
    paper exists as a 1982 journal article and a 1988 book chapter, and other
    sources report a later reissue year. Placing it on a timeline at the wrong
    one misrepresents when the idea appeared, so the choice is surfaced rather
    than guessed.
    """
    search = clean_title_for_search(title)
    if not search:
        return []
    params: dict[str, Any] = {
        "filter": f"title.search:{search}",
        "per-page": 10,
        "select": "id,doi,title,publication_year,type,cited_by_count,primary_location",
    }
    if email:
        params["mailto"] = email

    data = await get_json(client, f"{BASE}/works", source="openalex", params=params)
    wanted = "".join(ch for ch in search.lower() if ch.isalnum())
    out = []
    for w in (data or {}).get("results", []):
        got = "".join(
            ch for ch in clean_title_for_search(w.get("title") or "").lower() if ch.isalnum()
        )
        if not got or (got != wanted and not got.startswith(wanted[:60])
                       and not wanted.startswith(got[:60])):
            continue
        oa_id = norm_openalex(w.get("id"))
        if exclude and oa_id == exclude:
            continue
        source = (w.get("primary_location") or {}).get("source") or {}
        out.append(
            {
                "openalex_id": oa_id,
                "doi": ids.norm_doi(w.get("doi")),
                "title": clean_title(w.get("title")),
                "year": w.get("publication_year"),
                "type": w.get("type"),
                "venue": source.get("display_name"),
                "citation_count": w.get("cited_by_count"),
            }
        )
    out.sort(key=lambda r: (r["year"] or 9999))
    return out


async def fetch_author(
    client: httpx.AsyncClient, author_id: str, email: str
) -> dict[str, Any] | None:
    """Career-level figures for one author: output, citations, h-index, topics."""
    data = await get_json(
        client, f"{BASE}/authors/{author_id}",
        source="openalex", params={"mailto": email} if email else None,
    )
    if not data:
        return None
    stats = data.get("summary_stats") or {}
    institutions = data.get("last_known_institutions") or []
    return {
        "openalex_id": norm_openalex(data.get("id")),
        "name": data.get("display_name"),
        "orcid": ids.norm_orcid(data.get("orcid")),
        "works_count": data.get("works_count"),
        "cited_by_count": data.get("cited_by_count"),
        "h_index": stats.get("h_index"),
        "i10_index": stats.get("i10_index"),
        "affiliation": institutions[0].get("display_name") if institutions else None,
        "country_code": institutions[0].get("country_code") if institutions else None,
        "topics": [t.get("display_name") for t in (data.get("topics") or [])[:6]],
        "alternate_names": data.get("display_name_alternatives") or [],
    }


async def fetch_author_works(
    client: httpx.AsyncClient, author_id: str, email: str, limit: int = 200
) -> list[dict[str, Any]]:
    """Everything OpenAlex attributes to this author, most cited first."""
    out: list[dict[str, Any]] = []
    cursor = "*"
    while len(out) < limit and cursor:
        params: dict[str, Any] = {
            "filter": f"author.id:{author_id}",
            "per-page": min(200, limit - len(out)),
            "cursor": cursor,
            "sort": "cited_by_count:desc",
            "select": "id,doi,title,publication_year,type,cited_by_count,primary_location,authorships",
        }
        if email:
            params["mailto"] = email
        data = await get_json(client, f"{BASE}/works", source="openalex", params=params)
        if not data:
            break
        for w in data.get("results", []):
            source = (w.get("primary_location") or {}).get("source") or {}
            names = [
                ((a.get("author") or {}).get("display_name") or "")
                for a in (w.get("authorships") or [])[:6]
            ]
            out.append(
                {
                    "openalex_id": norm_openalex(w.get("id")),
                    "doi": ids.norm_doi(w.get("doi")),
                    "title": clean_title(w.get("title")),
                    "year": w.get("publication_year"),
                    "type": w.get("type"),
                    "venue": source.get("display_name"),
                    "citation_count": w.get("cited_by_count"),
                    "authors_blob": ", ".join(n for n in names if n) or None,
                }
            )
        cursor = (data.get("meta") or {}).get("next_cursor")
    return out


async def fetch_institutions(
    client: httpx.AsyncClient, oa_ids: list[str], email: str
) -> dict[str, dict[str, Any]]:
    """Coordinates for institutions, keyed by OpenAlex id, 50 per request."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(oa_ids), 50):
        chunk = [x for x in oa_ids[i : i + 50] if x]
        if not chunk:
            continue
        params: dict[str, Any] = {
            "filter": "openalex_id:" + "|".join(chunk),
            "per-page": 50,
            "select": "id,display_name,country_code,type,geo,ror",
        }
        if email:
            params["mailto"] = email
        data = await get_json(client, f"{BASE}/institutions", source="openalex", params=params)
        for inst in (data or {}).get("results", []):
            geo = inst.get("geo") or {}
            key = norm_openalex(inst.get("id"))
            if key:
                out[key] = {
                    "name": inst.get("display_name"),
                    "country_code": geo.get("country_code") or inst.get("country_code"),
                    "city": geo.get("city"),
                    "lat": geo.get("latitude"),
                    "lon": geo.get("longitude"),
                }
    return out


async def fetch_citing(
    client: httpx.AsyncClient, oa_id: str, email: str, limit: int = 200
) -> list[dict[str, Any]]:
    """Works citing `oa_id`, as lightweight reference records."""
    out: list[dict[str, Any]] = []
    cursor = "*"
    while len(out) < limit and cursor:
        params = {
            "filter": f"cites:{oa_id}",
            "per-page": min(200, limit - len(out)),
            "cursor": cursor,
            "select": "id,doi,title,publication_year,cited_by_count,authorships",
        }
        if email:
            params["mailto"] = email
        data = await get_json(client, f"{BASE}/works", source="openalex", params=params)
        if not data:
            break
        for w in data.get("results", []):
            names = [
                ((a.get("author") or {}).get("display_name") or "")
                for a in (w.get("authorships") or [])[:5]
            ]
            out.append(
                {
                    "doi": ids.norm_doi(w.get("doi")),
                    "openalex_id": ids.norm_openalex(w.get("id")),
                    "s2_id": None,
                    "arxiv_id": None,
                    "title": clean_title(w.get("title")),
                    "year": w.get("publication_year"),
                    "authors_blob": ", ".join(n for n in names if n) or None,
                    "citation_count": w.get("cited_by_count"),
                    "context": None,
                    "intent": None,
                    "is_influential": 0,
                }
            )
        cursor = (data.get("meta") or {}).get("next_cursor")
    return out
