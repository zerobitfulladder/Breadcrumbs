"""Identifier normalisation.

Every source spells identifiers differently. OpenAlex returns full URLs, Crossref
returns bare DOIs in mixed case, arXiv ids appear with and without a version
suffix. Link resolution compares these strings directly, so they must be
normalised on the way in or matches are silently missed.
"""
from __future__ import annotations

import re
from typing import Any

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")
_ARXIV_RE = re.compile(r"(?:arxiv[:/])?(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)


def norm_doi(value: Any) -> str | None:
    """Lowercase, strip any resolver prefix. DOIs are case-insensitive."""
    if not value:
        return None
    s = str(value).strip()
    low = s.lower()
    for p in _DOI_PREFIXES:
        if low.startswith(p):
            s = s[len(p):]
            break
    s = s.strip().lower()
    return s or None if s.startswith("10.") else None


def norm_arxiv(value: Any) -> str | None:
    """Strip the version suffix so 2301.00001v3 and 2301.00001 match."""
    if not value:
        return None
    m = _ARXIV_RE.search(str(value).strip())
    return m.group(1).lower() if m else None


def norm_openalex(value: Any) -> str | None:
    """OpenAlex ids come as https://openalex.org/W123; keep the bare W-id."""
    if not value:
        return None
    s = str(value).strip().rstrip("/")
    return (s.rsplit("/", 1)[-1] or None) if s else None


def norm_orcid(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip().rstrip("/")
    return s.rsplit("/", 1)[-1] or None


def norm_s2(value: Any) -> str | None:
    if not value:
        return None
    return str(value).strip() or None


def norm_name(value: Any) -> str | None:
    """For author dedupe: casefold and squeeze whitespace and punctuation."""
    if not value:
        return None
    s = re.sub(r"[.’']", "", str(value))
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s or None


def norm_pmid(value: Any) -> str | None:
    """PubMed ids are digits; strip any prefix a source adds."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def identity_keys(rec: dict[str, Any]) -> dict[str, str | None]:
    """The identifiers used for matching, normalised.

    Every one of these is UNIQUE on `papers`, so all of them must be checked:
    matching on a subset lets a paper look new, and the insert then fails on
    the constraint for whichever identifier was skipped.
    """
    return {
        "doi": norm_doi(rec.get("doi")),
        "arxiv_id": norm_arxiv(rec.get("arxiv_id")),
        "s2_id": norm_s2(rec.get("s2_id")),
        "openalex_id": norm_openalex(rec.get("openalex_id")),
        "pmid": norm_pmid(rec.get("pmid")),
    }
