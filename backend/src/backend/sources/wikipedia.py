"""Wikipedia adapter: a short biography and portrait for an author.

Name search alone is dangerous here. Searching Wikipedia for "Luke Marris"
returns the Faulkner novel *As I Lay Dying* as its top hit, so taking the first
result would present a novel as somebody's biography. Every candidate is
therefore checked against the author's name before it is accepted, and a page
that does not clearly match is reported as "no match" rather than guessed at.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx

from .http import get_json

API = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIDATA = "https://www.wikidata.org/w/api.php"

# Wikidata "instance of" (P31) = "human" (Q5). This is the authority on whether
# a page is about a person. A keyword blocklist is not: searching "David Field"
# returns "Harry & David Field", a baseball park, whose name matches perfectly.
P_INSTANCE_OF = "P31"
Q_HUMAN = "Q5"


def name_tokens(name: str) -> list[str]:
    """Lowercase word tokens, with initials and particles dropped.

    Initials are dropped so "Bruno A. Olshausen" still matches the page titled
    "Bruno Olshausen".
    """
    words = re.findall(r"[^\W\d_]+", (name or "").lower(), re.UNICODE)
    return [w for w in words if len(w) > 1 and w not in {"van", "von", "de", "der", "den", "di"}]


def is_same_person(author: str, title: str) -> bool:
    """Whether a page title plausibly names this author.

    Requires the surname to match outright, plus a shared given name whenever
    both sides have one. That is strict enough to reject the unrelated pages
    Wikipedia's search returns for names it does not know.
    """
    a = name_tokens(author)
    t = name_tokens(title)
    if not a or not t or a[-1] != t[-1]:
        return False
    if len(a) == 1 or len(t) == 1:
        return True
    return bool(set(a[:-1]) & set(t[:-1]))


async def _wikidata_ids(client: httpx.AsyncClient, titles: list[str]) -> dict[str, str]:
    """Map page titles to their Wikidata item ids, in one request."""
    if not titles:
        return {}
    data = await get_json(
        client, API, source="wikipedia",
        params={
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "titles": "|".join(titles[:20]), "format": "json", "redirects": 1,
        },
    )
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out: dict[str, str] = {}
    for page in pages.values():
        qid = (page.get("pageprops") or {}).get("wikibase_item")
        if page.get("title") and qid:
            out[page["title"]] = qid
    return out


async def _humans(client: httpx.AsyncClient, qids: list[str]) -> set[str]:
    """Of these Wikidata items, which are people."""
    if not qids:
        return set()
    data = await get_json(
        client, WIKIDATA, source="wikipedia",
        params={
            "action": "wbgetentities", "ids": "|".join(qids[:50]),
            "props": "claims", "format": "json",
        },
    )
    human = set()
    for qid, entity in ((data or {}).get("entities") or {}).items():
        claims = (entity.get("claims") or {}).get(P_INSTANCE_OF) or []
        for claim in claims:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            if value.get("id") == Q_HUMAN:
                human.add(qid)
                break
    return human


async def _summary(client: httpx.AsyncClient, title: str) -> dict[str, Any] | None:
    slug = urllib.parse.quote(title.replace(" ", "_"), safe="")
    return await get_json(client, f"{SUMMARY}/{slug}", source="wikipedia")


# Wikidata properties worth stating about a researcher.
_FACTS = {
    "P106": "occupations",
    "P108": "employers",
    "P69": "education",
    "P166": "awards",
    "P569": "born",
}


async def fetch_facts(client: httpx.AsyncClient, qid: str) -> dict[str, Any]:
    """Structured facts from Wikidata: what they do, where, and what they won.

    Preferred over the Wikipedia lead paragraph where both exist. These are
    discrete claims that can be shown and checked one by one, rather than prose
    that has to be trusted whole.
    """
    data = await get_json(
        client, WIKIDATA, source="wikipedia",
        params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
    )
    claims = (((data or {}).get("entities") or {}).get(qid) or {}).get("claims") or {}

    wanted: dict[str, list[str]] = {}
    labels_needed: set[str] = set()
    for prop, key in _FACTS.items():
        for claim in claims.get(prop, [])[:8]:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, dict) and value.get("id"):
                wanted.setdefault(key, []).append(value["id"])
                labels_needed.add(value["id"])
            elif isinstance(value, dict) and value.get("time"):
                wanted.setdefault(key, []).append(value["time"].lstrip("+")[:10])

    # One request resolves every referenced item to a readable label.
    labels: dict[str, str] = {}
    if labels_needed:
        batch = await get_json(
            client, WIKIDATA, source="wikipedia",
            params={
                "action": "wbgetentities", "ids": "|".join(sorted(labels_needed)[:50]),
                "props": "labels", "languages": "en", "format": "json",
            },
        )
        for qid_, entity in ((batch or {}).get("entities") or {}).items():
            label = ((entity.get("labels") or {}).get("en") or {}).get("value")
            if label:
                labels[qid_] = label

    out: dict[str, Any] = {"wikidata_id": qid}
    for key, values in wanted.items():
        resolved = [labels.get(v, v) for v in values]
        out[key] = sorted({r for r in resolved if not r.startswith("Q")})
    if out.get("born"):
        out["born"] = out["born"][0]
    return out


async def fetch_profile(
    client: httpx.AsyncClient, name: str, hint: str | None = None
) -> dict[str, Any]:
    """Look up one author. Always returns a record, including for a miss.

    `hint` is an affiliation, tried only as a fallback. Folding it into the
    first query actively hurts: searching "Timothy Lillicrap Google DeepMind
    (United Kingdom)" buries the page that a plain name search finds first.
    """

    async def search(term: str) -> list[str]:
        data = await get_json(
            client, API, source="wikipedia",
            params={
                "action": "query", "list": "search", "srsearch": term,
                "srlimit": 5, "format": "json",
            },
        )
        return [h.get("title") for h in ((data or {}).get("query") or {}).get("search", [])]

    hits = await search(name)
    candidates = [t for t in hits if t and is_same_person(name, t)]

    # Only if the plain name found nothing is it worth adding the affiliation,
    # which can surface a page for a common name.
    if not candidates and hint:
        extra = await search(f"{name} {hint}")
        candidates = [t for t in extra if t and is_same_person(name, t)]

    if not candidates:
        return {
            "status": "none",
            "detail": (
                f"No Wikipedia page matches '{name}'. Most researchers do not have one."
            ),
        }

    # Keep only pages Wikidata says are about a person. Two extra requests, and
    # they remove the whole class of name-alike false positives: stadiums,
    # novels, bands, places.
    qids = await _wikidata_ids(client, candidates)
    human_qids = await _humans(client, list(qids.values()))
    people = [t for t in candidates if qids.get(t) in human_qids]

    if not people:
        return {
            "status": "none",
            "detail": (
                f"Wikipedia has pages named like '{name}', but none of them are about "
                "a person."
            ),
        }

    for title in people:
        page = await _summary(client, title)
        if not page or page.get("type") != "standard":
            continue
        description = page.get("description") or ""
        extract = page.get("extract") or ""

        thumb = (page.get("thumbnail") or {}).get("source")
        original = (page.get("originalimage") or {}).get("source")
        facts = {}
        qid = qids.get(title)
        if qid:
            try:
                facts = await fetch_facts(client, qid)
            except SourceError:
                facts = {}
        return {
            "facts": facts,
            "status": "found",
            "title": page.get("title"),
            "url": ((page.get("content_urls") or {}).get("desktop") or {}).get("page"),
            "description": description or None,
            "extract": extract or None,
            "thumbnail_url": thumb,
            "image_url": original or thumb,
            "detail": "",
        }

    return {
        "status": "none",
        "detail": f"Wikipedia pages matching '{name}' are not biographies.",
    }
