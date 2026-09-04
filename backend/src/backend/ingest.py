"""Add one paper to the library, by DOI or another identifier.

Adding is always deliberate: exactly one paper row is created per call. The
paper's references and citations are stored as identifiers only, so links form
later as you add the other papers yourself.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from . import db, ids, store
from .sources import arxiv, crossref, openalex, s2, unpaywall
from .sources.http import SourceError, make_client

log = logging.getLogger(__name__)

# Fields only Semantic Scholar supplies, so S2 wins them outright.
_S2_ONLY = ("influential_citation_count",)
# Merge order for everything else: first non-null wins.
_MERGE_ORDER = ("openalex", "s2", "crossref")


@dataclass
class Bundle:
    """Everything fetched for one identifier, before anything is written.

    Held so the preview the user approves is exactly what gets saved, rather
    than re-querying and risking a different answer.
    """

    kind: str
    value: str
    by_source: dict[str, dict[str, Any]]
    outcomes: dict[str, "SourceOutcome"]
    merged: dict[str, Any]
    references: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    versions: list[dict[str, Any]] = field(default_factory=list)
    edge_notes: dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0


_PREVIEW_CACHE: dict[str, Bundle] = {}
_PREVIEW_TTL = 900.0  # seconds


def cache_bundle(bundle: Bundle) -> str:
    token = f"{bundle.kind}:{bundle.value}"
    bundle.fetched_at = time.monotonic()
    _PREVIEW_CACHE[token] = bundle
    for key, value in list(_PREVIEW_CACHE.items()):
        if time.monotonic() - value.fetched_at > _PREVIEW_TTL:
            _PREVIEW_CACHE.pop(key, None)
    return token


def cached_bundle(token: str) -> Bundle | None:
    bundle = _PREVIEW_CACHE.get(token)
    if bundle and time.monotonic() - bundle.fetched_at <= _PREVIEW_TTL:
        return bundle
    _PREVIEW_CACHE.pop(token, None)
    return None


@dataclass
class IngestResult:
    paper_id: int | None = None
    created: bool = False
    title: str | None = None
    sources_used: list[str] = field(default_factory=list)
    references_stored: int = 0
    citations_stored: int = 0
    links_created: int = 0
    pdf_path: str | None = None
    #: A PDF worth fetching once the response has gone out.
    pdf_pending: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "created": self.created,
            "title": self.title,
            "sources_used": self.sources_used,
            "references_stored": self.references_stored,
            "citations_stored": self.citations_stored,
            "links_created": self.links_created,
            "pdf_path": self.pdf_path,
            "pdf_pending": bool(self.pdf_pending),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# input parsing
# ---------------------------------------------------------------------------
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/\S+", re.I)


def parse_identifier(raw: str) -> tuple[str, str] | None:
    """Classify user input as ('doi'|'arxiv'|'openalex'|'s2', value)."""
    text = (raw or "").strip()
    if not text:
        return None

    doi = ids.norm_doi(text)
    if doi:
        return ("doi", doi.rstrip(".,;)"))

    m = _DOI_IN_TEXT.search(text)
    if m:
        return ("doi", ids.norm_doi(m.group(0).rstrip(".,;)")) or m.group(0).lower())

    if "arxiv.org" in text.lower() or text.lower().startswith("arxiv:"):
        ax = ids.norm_arxiv(text)
        if ax:
            return ("arxiv", ax)

    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", text):
        return ("arxiv", ids.norm_arxiv(text) or text)

    if re.fullmatch(r"W\d+", text, re.I) or "openalex.org" in text.lower():
        oa = ids.norm_openalex(text)
        if oa:
            return ("openalex", oa)

    if re.fullmatch(r"[0-9a-f]{40}", text, re.I):
        return ("s2", text.lower())

    return None


# ---------------------------------------------------------------------------
# merging
# ---------------------------------------------------------------------------
def _merge_authors(by_source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAlex authors are the base because only they carry institutions.

    Semantic Scholar author ids are grafted on by name, which is why the ids
    module normalises names: "T. Lillicrap" and "Timothy Lillicrap" would
    otherwise never match.
    """
    base: list[dict[str, Any]] = []
    for source in _MERGE_ORDER:
        authors = (by_source.get(source) or {}).get("authors") or []
        if len(authors) > len(base):
            base = [dict(a) for a in authors]

    s2_authors = (by_source.get("s2") or {}).get("authors") or []
    if s2_authors and base:
        by_name = {ids.norm_name(a.get("name")): a for a in s2_authors}
        by_last: dict[str, dict[str, Any]] = {}
        for a in s2_authors:
            norm = ids.norm_name(a.get("name")) or ""
            if norm:
                by_last.setdefault(norm.split()[-1], a)
        for author in base:
            norm = ids.norm_name(author.get("name")) or ""
            match = by_name.get(norm) or (by_last.get(norm.split()[-1]) if norm else None)
            if match and not author.get("s2_author_id"):
                author["s2_author_id"] = match.get("s2_author_id")
    return base


def merge_records(by_source: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in _MERGE_ORDER:
        rec = by_source.get(source)
        if not rec:
            continue
        for key, value in rec.items():
            if key in ("authors", "topics", "referenced_works", "locations"):
                continue
            if value in (None, "", []):
                continue
            if key in _S2_ONLY and source != "s2":
                continue
            merged.setdefault(key, value)

    s2_rec = by_source.get("s2") or {}
    for key in _S2_ONLY:
        if s2_rec.get(key) not in (None, "", []):
            merged[key] = s2_rec[key]

    merged["authors"] = _merge_authors(by_source)
    merged["topics"] = (by_source.get("openalex") or {}).get("topics") or []
    merged["locations"] = (by_source.get("openalex") or {}).get("locations") or []

    oa = by_source.get("unpaywall") or {}
    if oa:
        if oa.get("is_oa"):
            merged["is_oa"] = True
            merged["oa_pdf_url"] = oa.get("oa_pdf_url") or merged.get("oa_pdf_url")
            merged["oa_status"] = oa.get("oa_status") or merged.get("oa_status")
            merged["license"] = merged.get("license") or oa.get("license")
        elif not merged.get("oa_pdf_url"):
            merged["is_oa"] = False

    if merged.get("arxiv_id") and not merged.get("oa_pdf_url"):
        merged["oa_pdf_url"] = arxiv.pdf_url(merged["arxiv_id"])
        merged["is_oa"] = True

    merged["sources"] = [s for s in by_source if by_source.get(s)]
    return merged


def _edge_keys(rec: dict[str, Any]) -> list[str]:
    keys = ids.identity_keys(rec)
    return [f"{col}:{keys[col]}" for col in ("doi", "s2_id", "openalex_id", "arxiv_id") if keys[col]]


def merge_edge_lists(
    primary: list[dict[str, Any]], secondary: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Combine two reference/citation lists, de-duplicated on any shared id.

    Semantic Scholar contributes the citation context and intent; OpenAlex
    contributes coverage and DOIs. Neither alone is complete: S2 returns no
    references at all for some journals, and OpenAlex carries no context.
    """
    merged: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    for source in (primary, secondary):
        for rec in source:
            hit = next((index[k] for k in _edge_keys(rec) if k in index), None)
            if hit is None:
                merged.append(dict(rec))
                position = len(merged) - 1
            else:
                position = hit
                target = merged[position]
                for key, value in rec.items():
                    if target.get(key) in (None, "", 0) and value not in (None, "", []):
                        target[key] = value
            for k in _edge_keys(merged[position]):
                index.setdefault(k, position)
    return merged


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
@dataclass
class SourceOutcome:
    """What one API did on this lookup, success or not.

    Kept even for failures so the Add screen can show a panel per source
    rather than silently dropping the ones that did not answer.
    """

    name: str
    status: str = "skipped"   # ok | empty | error | skipped
    detail: str = ""
    ms: int = 0
    record: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        rec = self.record or {}
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "ms": self.ms,
            "fields": sorted(
                k for k, v in rec.items()
                if v not in (None, "", []) and k not in ("referenced_works",)
            ),
            "title": rec.get("title"),
            "year": rec.get("year"),
            "venue": rec.get("venue"),
            "author_count": len(rec.get("authors") or []),
            "has_abstract": bool(rec.get("abstract")),
            "record": rec or None,
        }


ALL_SOURCES = ("openalex", "s2", "crossref", "unpaywall")


async def _run_source(name: str, coro: Any) -> SourceOutcome:
    """Await one source call, converting any failure into an outcome."""
    started = time.monotonic()
    try:
        record = await coro
    except SourceError as exc:
        return SourceOutcome(name, "error", str(exc), int((time.monotonic() - started) * 1000))
    except Exception as exc:  # a broken source must not fail the whole lookup
        log.exception("%s failed", name)
        return SourceOutcome(
            name, "error", f"{type(exc).__name__}: {exc}", int((time.monotonic() - started) * 1000)
        )
    ms = int((time.monotonic() - started) * 1000)
    if not record:
        return SourceOutcome(name, "empty", "no record for this identifier", ms)
    return SourceOutcome(name, "ok", "", ms, record)


async def _gather_sources(
    client: httpx.AsyncClient, kind: str, value: str, email: str, s2_key: str,
    title_hint: str | None = None,
) -> dict[str, SourceOutcome]:
    """Query every source that can answer, concurrently, and report each one.

    Only a DOI opens all four. Given an arXiv, OpenAlex or Semantic Scholar id
    we run what we can, and if that yields a DOI we immediately fan out to the
    DOI-only sources in a second concurrent round. That second round matters:
    picking a typeahead suggestion hands us a Semantic Scholar id, and without
    it a throttled Semantic Scholar would take the whole lookup down with it.
    """
    outcomes: dict[str, SourceOutcome] = {
        name: SourceOutcome(name, "skipped", "not queried for this identifier type")
        for name in ALL_SOURCES
    }

    def doi_round(doi: str, skip: set[str]) -> list[Any]:
        jobs = []
        if "openalex" not in skip:
            jobs.append(_run_source("openalex", openalex.fetch_by_doi(client, doi, email)))
        if "crossref" not in skip:
            jobs.append(_run_source("crossref", crossref.fetch_by_doi(client, doi, email)))
        if "unpaywall" not in skip:
            jobs.append(_run_source("unpaywall", unpaywall.fetch_oa(client, doi, email)))
        return jobs

    jobs: list[Any] = []
    if kind == "doi":
        jobs += doi_round(value, skip=set())
        jobs.append(_run_source("s2", s2.fetch_paper(client, f"DOI:{value}", s2_key)))
    elif kind == "arxiv":
        jobs.append(_run_source("s2", s2.fetch_paper(client, f"arXiv:{value}", s2_key)))
        jobs.append(
            _run_source("openalex", openalex.fetch_by_doi(client, f"10.48550/arxiv.{value}", email))
        )
    elif kind == "openalex":
        jobs.append(_run_source("openalex", openalex.fetch_by_id(client, value, email)))
    elif kind == "s2":
        jobs.append(_run_source("s2", s2.fetch_paper(client, value, s2_key)))

    for outcome in await asyncio.gather(*jobs):
        outcomes[outcome.name] = outcome

    # Recovery: the id lookup produced nothing, but the caller knows the title
    # (it came from the typeahead), so resolve on that instead. This is what
    # keeps a throttled Semantic Scholar from sinking a suggestion click.
    if title_hint and not any(o.status == "ok" for o in outcomes.values()):
        recovered = await _run_source(
            "openalex", openalex.find_by_title(client, title_hint, email)
        )
        if recovered.status == "ok":
            recovered.detail = "matched on title after the id lookup failed"
            outcomes["openalex"] = recovered

    # Second round: a DOI learned from whichever source answered first.
    if kind != "doi":
        found = None
        for name in ("openalex", "s2"):
            rec = outcomes[name].record
            if rec:
                found = ids.norm_doi(rec.get("doi"))
                if found:
                    break
        if found:
            done = {n for n in ALL_SOURCES if outcomes[n].status == "ok"}
            for outcome in await asyncio.gather(*doi_round(found, skip=done)):
                outcomes[outcome.name] = outcome

    return outcomes


PDF_TIMEOUT_S = 25.0


async def download_pdf_later(paper_id: int, url: str) -> None:
    """Fetch a paper's PDF after it has been saved.

    Not part of the save. Open-access links rot, and a dead host does not
    refuse a connection — it simply never answers, so the request runs to its
    timeout. Doing this inline made adding a paper hang for the length of that
    timeout on nothing more than a stale link.
    """
    try:
        async with make_client() as client:
            resp = await client.get(url, timeout=PDF_TIMEOUT_S, follow_redirects=True)
            body = resp.content
        if resp.status_code >= 400 or not body.startswith(b"%PDF"):
            return
        rel = f"pdfs/{paper_id}.pdf"
        (db.library_root() / rel).write_bytes(body)
        with db.session() as conn:
            conn.execute(
                "UPDATE papers SET pdf_path = ? WHERE id = ? AND pdf_path IS NULL",
                (rel, paper_id),
            )
        log.info("stored pdf for paper %s (%d bytes)", paper_id, len(body))
    except Exception as exc:
        # A missing PDF is normal and the paper is already saved; there is
        # nothing here worth interrupting the user for.
        log.info("no pdf for paper %s: %s", paper_id, exc)


async def _download_pdf(
    client: httpx.AsyncClient, url: str, paper_id: int, warnings: list[str]
) -> str | None:
    try:
        resp = await client.get(url, timeout=PDF_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        warnings.append(f"pdf download failed: {exc}")
        return None

    body = resp.content
    if not body.startswith(b"%PDF"):
        warnings.append("pdf download skipped: response was not a PDF")
        return None

    rel = f"pdfs/{paper_id}.pdf"
    (db.library_root() / rel).write_bytes(body)
    return rel


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
async def fetch_bundle(
    conn: sqlite3.Connection, raw_identifier: str, title_hint: str | None = None
) -> Bundle:
    """Query every source and merge, writing nothing. Backs the preview screen."""
    parsed = parse_identifier(raw_identifier)
    if parsed is None:
        raise ValueError(
            "Could not read that as an identifier. Give a DOI (10.xxxx/yyyy), "
            "an arXiv id, an OpenAlex id, or a URL containing one."
        )
    kind, value = parsed

    settings = db.get_settings(conn)
    email = settings.get("contact_email", "").strip()
    s2_key = settings.get("semantic_scholar_key", "").strip()
    warnings: list[str] = []
    if not email:
        warnings.append(
            "No contact email set. OpenAlex and Crossref throttle harder and "
            "Unpaywall is unavailable. Set one in Settings."
        )

    async with make_client() as client:
        outcomes = await _gather_sources(client, kind, value, email, s2_key, title_hint)
        by_source = {n: o.record for n, o in outcomes.items() if o.status == "ok" and o.record}
        for outcome in outcomes.values():
            if outcome.status == "error":
                warnings.append(f"{outcome.name}: {outcome.detail}")

        if not by_source:
            # Distinguish "this identifier does not exist" from "the source
            # refused to answer": those are fixed in completely different ways.
            throttled = any(
                "429" in o.detail for o in outcomes.values() if o.status == "error"
            )
            detail = "; ".join(
                f"{o.name} {o.status}" + (f" ({o.detail})" if o.detail else "")
                for o in outcomes.values() if o.status in ("error", "empty")
            )
            if throttled:
                raise LookupError(
                    "Semantic Scholar is rate limiting this session, and it was the only "
                    "source able to resolve this identifier. Wait a moment, or add a free "
                    f"API key in Settings to raise the limit. [{detail}]"
                )
            raise LookupError(f"No source could resolve {kind} '{value}'. [{detail}]")

        merged = merge_records(by_source)
        if not merged.get("title"):
            raise LookupError("Sources returned no title; refusing to store the record.")

        references: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        s2_ident = merged.get("s2_id") or (f"DOI:{merged['doi']}" if merged.get("doi") else None)
        limit = int(settings.get("citation_page_limit") or 500)

        if s2_ident:
            if settings.get("fetch_references", "1") == "1":
                try:
                    references = await s2.fetch_references(client, s2_ident, s2_key, limit)
                except SourceError as exc:
                    warnings.append(f"references unavailable: {exc}")
            if settings.get("fetch_citations", "1") == "1":
                try:
                    citations = await s2.fetch_citations(client, s2_ident, s2_key, limit)
                except SourceError as exc:
                    warnings.append(f"Semantic Scholar citations unavailable: {exc}")

        # Citing papers from OpenAlex. Without a Semantic Scholar key that
        # source throttles constantly, and OpenAlex covers the same edges; what
        # is lost is the context sentence and intent, not the graph itself.
        oa_id = merged.get("openalex_id")
        if oa_id and settings.get("fetch_citations", "1") == "1" and len(citations) < limit:
            try:
                oa_citing = await openalex.fetch_citing(client, oa_id, email, limit)
                before = len(citations)
                citations = merge_edge_lists(citations, oa_citing)
                if not before and citations:
                    warnings.append(
                        f"Citing papers came from OpenAlex ({len(citations)}); they carry no "
                        "citation context or intent, which only Semantic Scholar provides."
                    )
            except SourceError as exc:
                warnings.append(f"OpenAlex citations unavailable: {exc}")

        # OpenAlex returns references as bare W-ids. Resolve them to real
        # records so they carry DOIs and titles, then merge with the S2 list.
        oa_refs = (by_source.get("openalex") or {}).get("referenced_works") or []
        if oa_refs and settings.get("fetch_references", "1") == "1":
            try:
                works = await openalex.fetch_works_batch(client, oa_refs[:limit], email)
                oa_records = [
                    {
                        "doi": w.get("doi"),
                        "openalex_id": w.get("openalex_id"),
                        "s2_id": None,
                        "arxiv_id": w.get("arxiv_id"),
                        "title": w.get("title"),
                        "year": w.get("year"),
                        "authors_blob": ", ".join(
                            a["name"] for a in (w.get("authors") or [])[:5] if a.get("name")
                        ) or None,
                        "citation_count": w.get("citation_count"),
                        "context": None, "intent": None, "is_influential": 0,
                    }
                    for w in works
                ]
                references = merge_edge_lists(references, oa_records)
            except SourceError as exc:
                warnings.append(f"reference details unavailable: {exc}")
                if not references:
                    references = [{"openalex_id": x} for x in oa_refs]

    return Bundle(
        kind=kind, value=value, by_source=by_source, outcomes=outcomes, merged=merged,
        references=references, citations=citations, warnings=warnings,
    )


def count_incoming_links(conn: sqlite3.Connection, merged: dict[str, Any]) -> int:
    """Papers already held whose stored references name this one.

    Cheap — one query — and knowable as soon as the record is identified, so it
    does not need to wait for the reference lists to download.
    """
    # Restricted to the columns pending_links actually has: the identity set
    # also carries pmid, which lives only on `papers`.
    keys = ids.identity_keys(merged)
    clauses = [f"{col} = ?" for col in store.PENDING_COLUMNS if keys.get(col)]
    if not clauses:
        return 0
    args = [keys[col] for col in store.PENDING_COLUMNS if keys.get(col)]
    existing = store.find_paper_id(conn, merged)
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM pending_links WHERE resolved_paper_id IS NULL "
        f"AND ({' OR '.join(clauses)})" + (" AND from_paper_id != ?" if existing else ""),
        args + ([existing] if existing else []),
    ).fetchone()
    return int(row["c"])


def count_outgoing_links(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    """How many of these referenced papers are already in the library."""
    return sum(1 for rec in records if store.find_paper_id(conn, rec) is not None)


def preview_payload(conn: sqlite3.Connection, bundle: Bundle) -> dict[str, Any]:
    """Shape a bundle for the preview UI: merged record, per-source breakdown,
    and how many links would appear immediately if this were saved."""
    merged = bundle.merged
    existing_id = store.find_paper_id(conn, merged)

    per_source = {name: outcome.as_dict() for name, outcome in bundle.outcomes.items()}

    # Links that appear the moment you save, counted from both directions:
    #  - outgoing: this paper's references/citations already in the library
    #  - incoming: papers already here whose stored references/citations name
    #    this paper. Missing this second side under-reported the real total.
    outgoing = count_outgoing_links(conn, bundle.references + bundle.citations)
    incoming = count_incoming_links(conn, merged)
    would_link = outgoing + incoming

    return {
        "token": cache_bundle(bundle),
        "identifier": {"kind": bundle.kind, "value": bundle.value},
        "already_in_library": existing_id is not None,
        "existing_paper_id": existing_id,
        "merged": merged,
        "sources": per_source,
        "source_order": list(ALL_SOURCES),
        "source_names": sorted(bundle.by_source),
        "counts": {
            "references": len(bundle.references),
            "citations": len(bundle.citations),
            "authors": len(merged.get("authors") or []),
            "would_link_now": would_link,
            "would_link_outgoing": outgoing,
            "would_link_incoming": incoming,
        },
        "versions": bundle.versions,
        "earlier_version_year": next(
            (
                v["year"] for v in bundle.versions
                if v.get("year") and merged.get("year") and v["year"] < merged["year"]
            ),
            None,
        ),
        "edge_notes": bundle.edge_notes,
        "refetchable": list(REFETCHABLE),
        "sample_citations": bundle.citations[:20],
        "sample_references": bundle.references[:20],
        "warnings": bundle.warnings,
    }


REFETCHABLE = ALL_SOURCES + ("references", "citations")


async def stream_bundle(
    raw_identifier: str, title_hint: str | None = None
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run a lookup, yielding (event, data) as each stage lands.

    Same work as fetch_bundle, but reported incrementally so the Add screen can
    show four panels immediately and fill each one the moment its source
    answers, instead of blanking until the slowest has finished.
    """
    parsed = parse_identifier(raw_identifier)
    if parsed is None:
        yield "error", {
            "message": (
                "Could not read that as an identifier. Give a DOI (10.xxxx/yyyy), "
                "an arXiv id, an OpenAlex id, or a URL containing one."
            ),
            "kind": "bad_identifier",
        }
        return
    kind, value = parsed

    with db.session() as conn:
        settings = db.get_settings(conn)
    email = settings.get("contact_email", "").strip()
    s2_key = settings.get("semantic_scholar_key", "").strip()
    limit = int(settings.get("citation_page_limit") or 500)

    warnings: list[str] = []
    if not email:
        warnings.append(
            "No contact email set. OpenAlex and Crossref throttle harder and "
            "Unpaywall is unavailable. Set one in Settings."
        )

    outcomes: dict[str, SourceOutcome] = {
        name: SourceOutcome(name, "pending", "waiting") for name in ALL_SOURCES
    }
    yield "start", {
        "identifier": {"kind": kind, "value": value},
        "sources": [o.as_dict() for o in outcomes.values()],
        "source_order": list(ALL_SOURCES),
        "warnings": warnings,
    }

    async with make_client() as client:
        planned = _plan_jobs(client, kind, value, email, s2_key)
        for name in ALL_SOURCES:
            if name not in planned:
                outcomes[name] = SourceOutcome(
                    name, "skipped", "not queried for this identifier type"
                )
                yield "source", outcomes[name].as_dict()

        # Report each source the instant it finishes, not in launch order.
        for task in asyncio.as_completed(list(planned.values())):
            outcome = await task
            outcomes[outcome.name] = outcome
            yield "source", outcome.as_dict()

        # Title recovery, when nothing resolved from the identifier alone.
        if title_hint and not any(o.status == "ok" for o in outcomes.values()):
            yield "source", SourceOutcome("openalex", "pending", "retrying by title").as_dict()
            recovered = await _run_source(
                "openalex", openalex.find_by_title(client, title_hint, email)
            )
            if recovered.status == "ok":
                recovered.detail = "matched on title after the id lookup failed"
            outcomes["openalex"] = recovered
            yield "source", recovered.as_dict()

        # Second round: DOI-only sources, now that a DOI may be known.
        if kind != "doi":
            found = None
            for name in ("openalex", "s2"):
                rec = outcomes[name].record
                if rec and ids.norm_doi(rec.get("doi")):
                    found = ids.norm_doi(rec["doi"])
                    break
            if found:
                second = _plan_doi_jobs(
                    client, found, email,
                    skip={n for n in ALL_SOURCES if outcomes[n].status == "ok"},
                )
                for name in second:
                    yield "source", SourceOutcome(name, "pending", "resolving by DOI").as_dict()
                for task in asyncio.as_completed(list(second.values())):
                    outcome = await task
                    outcomes[outcome.name] = outcome
                    yield "source", outcome.as_dict()

        by_source = {n: o.record for n, o in outcomes.items() if o.status == "ok" and o.record}
        warnings += [f"{o.name}: {o.detail}" for o in outcomes.values() if o.status == "error"]

        if not by_source:
            throttled = any("429" in o.detail or "rate limited" in o.detail
                            for o in outcomes.values() if o.status == "error")
            yield "error", {
                "message": (
                    "Semantic Scholar is rate limiting and was the only source able to resolve "
                    "this identifier. Retry it from its panel in a moment."
                    if throttled else
                    f"No source could resolve {kind} '{value}'."
                ),
                "kind": "throttled" if throttled else "not_found",
            }
            return

        merged = merge_records(by_source)
        if not merged.get("title"):
            yield "error", {"message": "Sources returned no title.", "kind": "no_title"}
            return
        with db.session() as counting:
            incoming = count_incoming_links(counting, merged)
        # Reported now rather than at the end: this half of the figure depends
        # only on the record, and waiting for both reference lists made the
        # panel look stuck on a number it already knew.
        yield "merged", {"merged": merged, "would_link_incoming": incoming}

        bundle = Bundle(
            kind=kind, value=value, by_source=by_source, outcomes=outcomes,
            merged=merged, warnings=warnings,
        )

        # The same work is often indexed more than once, under different years.
        # Surface the alternatives so the timeline can be given the real date.
        if merged.get("title"):
            version_error: str | None = None
            try:
                bundle.versions = await openalex.find_versions(
                    client, merged["title"], email, exclude=merged.get("openalex_id")
                )
            except SourceError as exc:
                bundle.versions = []
                version_error = str(exc)
            # Report a failed check rather than staying silent: silence reads as
            # "there are no other versions", which is what misdates a paper.
            if bundle.versions or version_error:
                yield "versions", {
                    "versions": bundle.versions,
                    "current_year": merged.get("year"),
                    "error": version_error,
                }

        # References and citations, each reported as it completes.
        if settings.get("fetch_references", "1") == "1":
            yield "edges_pending", {"which": "references"}
            await _refetch_edges(client, "references", bundle, email, s2_key, limit)
            with db.session() as counting:
                held = count_outgoing_links(counting, bundle.references)
            yield "edges", {
                "which": "references",
                "count": len(bundle.references),
                "note": bundle.edge_notes.get("references", ""),
                "sample": bundle.references[:20],
                "would_link_outgoing": held,
            }
        if settings.get("fetch_citations", "1") == "1":
            yield "edges_pending", {"which": "citations"}
            await _refetch_edges(client, "citations", bundle, email, s2_key, limit)
            with db.session() as counting:
                held = count_outgoing_links(counting, bundle.citations)
            yield "edges", {
                "which": "citations",
                "count": len(bundle.citations),
                "note": bundle.edge_notes.get("citations", ""),
                "sample": bundle.citations[:20],
                "would_link_outgoing": held,
            }

    with db.session() as conn:
        yield "done", preview_payload(conn, bundle)


def _plan_doi_jobs(
    client: httpx.AsyncClient, doi: str, email: str, skip: set[str]
) -> dict[str, Any]:
    jobs: dict[str, Any] = {}
    if "openalex" not in skip:
        jobs["openalex"] = _run_source("openalex", openalex.fetch_by_doi(client, doi, email))
    if "crossref" not in skip:
        jobs["crossref"] = _run_source("crossref", crossref.fetch_by_doi(client, doi, email))
    if "unpaywall" not in skip:
        jobs["unpaywall"] = _run_source("unpaywall", unpaywall.fetch_oa(client, doi, email))
    return jobs


def _plan_jobs(
    client: httpx.AsyncClient, kind: str, value: str, email: str, s2_key: str
) -> dict[str, Any]:
    if kind == "doi":
        jobs = _plan_doi_jobs(client, value, email, skip=set())
        jobs["s2"] = _run_source("s2", s2.fetch_paper(client, f"DOI:{value}", s2_key))
        return jobs
    if kind == "arxiv":
        return {
            "s2": _run_source("s2", s2.fetch_paper(client, f"arXiv:{value}", s2_key)),
            "openalex": _run_source(
                "openalex", openalex.fetch_by_doi(client, f"10.48550/arxiv.{value}", email)
            ),
        }
    if kind == "openalex":
        return {"openalex": _run_source("openalex", openalex.fetch_by_id(client, value, email))}
    return {"s2": _run_source("s2", s2.fetch_paper(client, value, s2_key))}


async def refetch_source(
    conn: sqlite3.Connection, bundle: Bundle, source: str
) -> Bundle:
    """Re-query one source and fold the result back into a cached bundle.

    Lets you retry a source that was throttled without discarding the three
    that answered. The bundle is mutated in place and stays under the same
    preview token, so the Add screen keeps its position.
    """
    if source not in REFETCHABLE:
        raise ValueError(f"'{source}' is not a refetchable source")

    settings = db.get_settings(conn)
    email = settings.get("contact_email", "").strip()
    s2_key = settings.get("semantic_scholar_key", "").strip()
    limit = int(settings.get("citation_page_limit") or 500)
    merged = bundle.merged
    doi = ids.norm_doi(merged.get("doi")) or (bundle.value if bundle.kind == "doi" else None)

    async with make_client() as client:
        if source in ALL_SOURCES:
            outcome = await _refetch_one(client, source, bundle, doi, email, s2_key)
            bundle.outcomes[source] = outcome
            bundle.by_source = {
                n: o.record for n, o in bundle.outcomes.items() if o.status == "ok" and o.record
            }
            if bundle.by_source:
                bundle.merged = merge_records(bundle.by_source)
        else:
            await _refetch_edges(client, source, bundle, email, s2_key, limit)

    bundle.warnings = [
        f"{o.name}: {o.detail}" for o in bundle.outcomes.values() if o.status == "error"
    ]
    cache_bundle(bundle)
    return bundle


async def _refetch_one(
    client: httpx.AsyncClient, source: str, bundle: Bundle,
    doi: str | None, email: str, s2_key: str,
) -> SourceOutcome:
    merged = bundle.merged
    if source == "openalex":
        oa_id = merged.get("openalex_id")
        if doi:
            call = openalex.fetch_by_doi(client, doi, email)
        elif oa_id:
            call = openalex.fetch_by_id(client, oa_id, email)
        elif merged.get("title"):
            call = openalex.find_by_title(client, merged["title"], email, merged.get("year"))
        else:
            return SourceOutcome(source, "skipped", "no identifier to query with")
    elif source == "s2":
        ident = merged.get("s2_id") or (f"DOI:{doi}" if doi else None)
        if not ident and merged.get("arxiv_id"):
            ident = f"arXiv:{merged['arxiv_id']}"
        if not ident:
            return SourceOutcome(source, "skipped", "no identifier to query with")
        call = s2.fetch_paper(client, ident, s2_key)
    elif source == "crossref":
        if not doi:
            return SourceOutcome(source, "skipped", "needs a DOI")
        call = crossref.fetch_by_doi(client, doi, email)
    elif source == "unpaywall":
        if not doi:
            return SourceOutcome(source, "skipped", "needs a DOI")
        if not email:
            return SourceOutcome(source, "skipped", "needs a contact email in Settings")
        call = unpaywall.fetch_oa(client, doi, email)
    else:
        return SourceOutcome(source, "skipped", "unknown source")

    return await _run_source(source, call)


async def _refetch_edges(
    client: httpx.AsyncClient, which: str, bundle: Bundle,
    email: str, s2_key: str, limit: int,
) -> None:
    """Pull the reference or citation list.

    OpenAlex is the source. It covers the same edges as Semantic Scholar, is
    not rate limited with a contact email set, and returns DOIs and titles that
    make each row matchable and readable. Semantic Scholar is a fallback for
    the rare paper OpenAlex does not hold.
    """
    merged = bundle.merged
    oa_id = merged.get("openalex_id")
    collected: list[dict[str, Any]] = []
    notes: list[str] = []

    if oa_id:
        try:
            if which == "references":
                work = await openalex.fetch_by_id(client, oa_id, email)
                refs = (work or {}).get("referenced_works") or []
                if refs:
                    works = await openalex.fetch_works_batch(client, refs[:limit], email)
                    collected = [_edge_from_work(w) for w in works]
                    notes.append(f"OpenAlex returned {len(collected)} of {len(refs)}")
            else:
                collected = await openalex.fetch_citing(client, oa_id, email, limit)
                if collected:
                    notes.append(f"OpenAlex returned {len(collected)}")
        except SourceError as exc:
            notes.append(f"OpenAlex failed ({exc})")

    if not collected:
        s2_ident = merged.get("s2_id")
        doi = ids.norm_doi(merged.get("doi"))
        if not s2_ident and doi:
            s2_ident = f"DOI:{doi}"
        if s2_ident:
            try:
                fetch = s2.fetch_references if which == "references" else s2.fetch_citations
                collected = await fetch(client, s2_ident, s2_key, limit)
                if collected:
                    notes.append(f"Semantic Scholar fallback returned {len(collected)}")
            except SourceError as exc:
                notes.append(f"Semantic Scholar fallback failed ({exc})")

    if which == "references":
        bundle.references = collected or bundle.references
    else:
        bundle.citations = collected or bundle.citations
    bundle.edge_notes[which] = "; ".join(notes) or "nothing returned"


def _edge_from_work(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "doi": w.get("doi"),
        "openalex_id": w.get("openalex_id"),
        "s2_id": None,
        "arxiv_id": w.get("arxiv_id"),
        "title": w.get("title"),
        "year": w.get("year"),
        "authors_blob": ", ".join(
            a["name"] for a in (w.get("authors") or [])[:5] if a.get("name")
        ) or None,
        "citation_count": w.get("citation_count"),
    }


async def save_bundle(conn: sqlite3.Connection, bundle: Bundle) -> IngestResult:
    """Commit a previously fetched bundle. Nothing is re-queried except the PDF."""
    result = IngestResult(warnings=list(bundle.warnings))
    settings = db.get_settings(conn)
    merged = bundle.merged

    paper_id, created = store.upsert_paper(conn, merged)
    result.paper_id, result.created = paper_id, created
    result.title = merged.get("title")
    result.sources_used = merged.get("sources") or sorted(bundle.by_source)

    result.references_stored = store.store_pending(conn, paper_id, "reference", bundle.references)
    result.citations_stored = store.store_pending(conn, paper_id, "citation", bundle.citations)
    result.links_created = store.resolve_links(conn, paper_id)

    pdf_url = merged.get("oa_pdf_url")
    row = conn.execute("SELECT pdf_path FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row and row["pdf_path"]:
        result.pdf_path = row["pdf_path"]
    elif settings.get("auto_download_pdf", "1") == "1" and pdf_url:
        # Handed to the caller to run after responding. The paper is saved
        # either way; the file is not worth waiting on.
        result.pdf_pending = pdf_url

    return result


async def add_paper(conn: sqlite3.Connection, raw_identifier: str) -> IngestResult:
    """Fetch and save in one step, for scripted use."""
    return await save_bundle(conn, await fetch_bundle(conn, raw_identifier))


async def search_papers(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Title search, for when you have a name rather than a DOI."""
    settings = db.get_settings(conn)
    async with make_client() as client:
        results = await s2.search(client, query, settings.get("semantic_scholar_key", ""), limit)

    for rec in results:
        rec["in_library"] = store.find_paper_id(conn, rec) is not None
    return results
