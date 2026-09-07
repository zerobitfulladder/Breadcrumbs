"""Find a PDF for a paper already in the library, narrating the search.

Nothing here runs on add. Adding a paper records metadata and stops; finding
the file is a separate act you ask for, and watch.

The old automatic fetch tried exactly one URL — the `oa_pdf_url` the metadata
merge had ranked best — and gave up silently when it failed. That URL is very
often a publisher landing page that serves HTML, so the fetch "worked" and
stored nothing, with no record of why. Both Unpaywall and OpenAlex actually
report *every* location they know of, and the copy that serves a real file is
usually further down the list. This walks all of them, in the order most likely
to yield a file, and says out loud what it tried and what came back.

Every candidate is verified by downloading it and checking the magic bytes. A
server that answers 200 with an access-denied page is a failure here, not a
stored file that breaks later in the reader.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx

from . import db
from .sources import arxiv, openalex, s2, unpaywall
from .sources.http import SourceError, make_client

log = logging.getLogger(__name__)

# A single candidate gets less time than the old inline fetch allowed, because
# here there are others waiting behind it. A dead host should cost the search a
# few seconds, not half a minute.
CANDIDATE_TIMEOUT_S = 15.0
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_CANDIDATES = 12
# Enough of a landing page to reach the <head> metadata. The link we want is
# always up there; the rest is article body and navigation.
MAX_HTML_BYTES = 512 * 1024
# A landing page may name the file, but a file must never name another page.
# Without a ceiling a chain of redirects between repositories could walk for
# ever, so only this many pages are ever read.
MAX_PAGES_READ = 4

# Lower sorts first. The ordering is about which links tend to be actual files:
# an arXiv PDF URL always is, a landing page usually is not.
_PRIORITY = {
    "arxiv": 0,          # an arXiv id on the record: certain to be this paper
    "page": 1,           # a link the landing page itself pointed at
    "unpaywall_pdf": 2,
    "openalex_pdf": 2,
    "s2_pdf": 3,
    "record": 4,
    # A title search is the one route that can find the *wrong* paper, so it
    # goes behind every link an identifier produced, however promising it looks.
    "arxiv_guess": 5,
    "landing": 6,        # usually a web page; tried last, and read for links
}


def _score(cand: "Candidate") -> float:
    """Sort key. Lower is tried sooner.

    Beyond the origin, one thing genuinely predicts whether a URL is a file:
    whether it looks like one. A link ending .pdf is worth trying before a link
    that does not, whatever produced it.
    """
    base = float(_PRIORITY.get(cand.kind, 9))
    path = urlparse(cand.url).path.lower()
    if path.endswith(".pdf") or "/pdf/" in path:
        base -= 1.5
    return base


@dataclass
class Candidate:
    url: str
    origin: str              # which lookup produced it
    kind: str                # key into _PRIORITY
    label: str               # shown to the user
    note: str | None = None  # repository name, version, licence...

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "origin": self.origin, "kind": self.kind,
            "label": self.label, "note": self.note,
        }


@dataclass
class _Found:
    candidates: list[Candidate] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def add(self, cand: Candidate | None) -> Candidate | None:
        """Keep a candidate unless the URL is already queued. Returns it if new."""
        if cand is None or not cand.url:
            return None
        url = cand.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        # Trailing punctuation and fragments make the same file look distinct.
        key = url.split("#", 1)[0].rstrip("/")
        if key in self.seen:
            return None
        self.seen.add(key)
        cand.url = url
        self.candidates.append(cand)
        return cand

    def ordered(self) -> list[Candidate]:
        return sorted(self.candidates, key=_score)[:MAX_CANDIDATES]


_WORD = re.compile(r"[^a-z0-9 ]+")


def _normalise(title: str) -> str:
    return " ".join(_WORD.sub(" ", (title or "").lower()).split())


def _title_matches(want: str, got: str) -> bool:
    """Whether an arXiv search hit is really the paper we are looking for.

    Storing the wrong paper is worse than finding nothing — it is a mistake you
    would not notice until you read it — so the bar is deliberately high.

    Containment is *not* enough, and that is the trap worth naming: searching
    for "Random synaptic feedback weights support error backpropagation" turns
    up "Iterative temporal differencing with random synaptic feedback weights
    support error backpropagation", a different and later paper that merely
    quotes the first in its own title. A longer title containing a shorter one
    usually means exactly that. So the two must be the same title, give or take
    punctuation and a word.
    """
    a, b = _normalise(want), _normalise(got)
    if not a or not b:
        return False
    if a == b:
        return True
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    # Jaccard rather than containment, so extra words on either side count
    # against the match instead of being free.
    overlap = len(wa & wb) / len(wa | wb)
    longer, shorter = max(len(wa), len(wb)), min(len(wa), len(wb))
    return overlap >= 0.9 and longer - shorter <= 1


# Every repository and most publishers publish the direct file link in the head
# of the landing page, under the name Google Scholar reads. Following it is what
# turns "the paper is open access somewhere" into an actual file.
_META_PDF = re.compile(r"<meta[^>]*citation_pdf_url[^>]*>", re.I)
_CONTENT = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)

# Repositories that hand out a landing URL and keep the file at a fixed offset
# from it. Cheap to try, and they cover most of what has no meta tag.
_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^https?://([\w.-]*\.)?hal\.science/[^/?#]+$", re.I), "{url}/document"),
    (re.compile(r"^https?://arxiv\.org/abs/(.+)$", re.I), "https://arxiv.org/pdf/{1}"),
    (re.compile(r"^https?://(?:www\.)?biorxiv\.org/content/(.+?)v(\d+)$", re.I),
     "https://www.biorxiv.org/content/{1}v{2}.full.pdf"),
    (re.compile(r"^(https?://citeseerx\.ist\.psu\.edu/viewdoc/)summary(\?doi=.+)$", re.I),
     "{1}download{2}"),
)


def _links_in_page(page: str, base_url: str) -> list[tuple[str, str]]:
    """Direct file links named by a landing page, as (url, how we found it)."""
    out: list[tuple[str, str]] = []
    for tag in _META_PDF.findall(page or ""):
        found = _CONTENT.search(tag)
        if found:
            out.append((urljoin(base_url, found.group(1).strip()), "named by the page itself"))
    return out


def _rewritten(url: str) -> tuple[str, str] | None:
    """A known repository's file URL derived from its landing URL."""
    for pattern, template in _REWRITES:
        m = pattern.match(url)
        if m:
            groups = {str(i + 1): g for i, g in enumerate(m.groups())}
            return template.format(url=url.rstrip("/"), **groups), "known repository layout"
    return None


def _paper_row(paper_id: int) -> sqlite3.Row | None:
    with db.session() as conn:
        return conn.execute(
            "SELECT id, title, doi, arxiv_id, pmid, openalex_id, url, is_oa, "
            "oa_pdf_url, pdf_path FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()


# Fingerprints of the interstitials that stand in for a file. These are not
# paywalls — the paper may be free — but a challenge page that wants JavaScript
# and cookies. Naming them matters: "HTTP 403" invites you to think the link is
# wrong, when in fact it works perfectly in a browser and only refuses a script.
_CHALLENGE_MARKS = (
    "_cf_chl_opt", "cf-browser-verification", "cf_chl_",     # Cloudflare
    "just a moment...", "attention required!",
    "checking your browser before accessing",
    "anubis", "making sure you're not a bot", "proof-of-work",  # Anubis
    "enable javascript and cookies", "verifying you are human",
    "verify you are human", "please enable javascript",
    "datadome", "perimeterx", "incapsula", "distil_r_captcha",
)


def _is_challenge(page: str) -> bool:
    """Whether this page is an anti-bot interstitial rather than content.

    Entities are unescaped first. These pages are generated, and several write
    the apostrophe in "you're" as &#39;, which a raw substring test misses —
    that is exactly how the Anubis challenge on one repository got reported as
    a bare "HTTP 403" and sent someone hunting for a redirect bug.
    """
    low = html.unescape(page[:4000]).lower()
    return any(mark in low for mark in _CHALLENGE_MARKS)


@dataclass
class Fetch:
    """What came back from one candidate URL."""

    body: bytes | None = None      # the file, when it really was one
    detail: str = ""               # what to show the person watching
    page: str | None = None        # the HTML, when the answer was a page
    blocked: bool = False          # a bot check stood in the way


async def _download(client: httpx.AsyncClient, url: str) -> Fetch:
    """Fetch one candidate and work out what the answer actually was.

    Redirects are followed — including the http→https hop these links usually
    start with — so a plain redirect never reaches the caller as a failure.

    The body is streamed, so an unexpectedly huge file is abandoned partway
    rather than pulled entirely into memory before being rejected. A page comes
    back as text whatever the status, because two useful things hide in one:
    repositories put the real download link in it, and publishers behind a bot
    check return the challenge itself, which is worth recognising rather than
    reporting as a bare status code.
    """
    try:
        async with client.stream(
            "GET", url, timeout=CANDIDATE_TIMEOUT_S, follow_redirects=True
        ) as resp:
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            status = resp.status_code
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                # An error body is only ever read for the clues in it, so it
                # needs a page's worth and no more.
                if total > (MAX_HTML_BYTES if status >= 400 else MAX_PDF_BYTES):
                    if status >= 400:
                        break
                    return Fetch(detail="larger than 200 MB")
            body = b"".join(chunks)
    except httpx.TimeoutException:
        return Fetch(detail=f"no answer within {CANDIDATE_TIMEOUT_S:.0f}s")
    except httpx.HTTPError as exc:
        return Fetch(detail=f"{type(exc).__name__}: {exc}")

    looks_html = body.lstrip()[:200].lower().startswith((b"<!doctype", b"<html"))
    page = (
        body[:MAX_HTML_BYTES].decode("utf-8", errors="replace")
        if looks_html or ctype == "text/html"
        else None
    )
    if page and _is_challenge(page):
        return Fetch(
            detail="blocked by a bot check — this link needs a browser",
            page=None,      # a challenge page names nothing worth following
            blocked=True,
        )

    if status >= 400:
        # The page still goes back: some publishers answer 403 with a perfectly
        # real landing page that names the file elsewhere.
        return Fetch(detail=f"HTTP {status}", page=page)
    if not body:
        return Fetch(detail="empty response")
    if not body.startswith(b"%PDF"):
        if page is not None:
            return Fetch(detail="served a web page, not a PDF", page=page)
        return Fetch(detail=f"served {ctype or 'an unknown type'}, not a PDF")
    return Fetch(body=body, detail=f"{len(body) / 1024 / 1024:.1f} MB")


def _store(paper_id: int, body: bytes, url: str) -> str:
    rel = f"pdfs/{paper_id}.pdf"
    (db.library_root() / rel).write_bytes(body)
    with db.session() as conn:
        # oa_pdf_url is overwritten with the link that actually produced a file.
        # What was there before was a guess that may well have been wrong — for
        # this library it was usually a landing page — and the working URL is
        # both a better answer for the "open-access copy" link and a better
        # starting point for the next search.
        conn.execute(
            "UPDATE papers SET pdf_path = ?, oa_pdf_url = ? WHERE id = ?",
            (rel, url, paper_id),
        )
    return rel


async def _lookup(name: str, coro: Any) -> tuple[str, Any, str | None]:
    """Run one source call, turning any failure into a reportable outcome."""
    try:
        return name, await coro, None
    except SourceError as exc:
        return name, None, str(exc)
    except Exception as exc:  # a source blowing up must not end the search
        log.info("pdf search: %s failed: %s", name, exc)
        return name, None, f"{type(exc).__name__}: {exc}"


async def search(paper_id: int) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Hunt for a PDF, yielding (event, data) as each step lands.

    Stops at the first candidate that proves to be a real PDF. Everything tried
    before it is reported, so a search that finds nothing still explains itself.
    """
    row = _paper_row(paper_id)
    if row is None:
        yield "error", {"message": "No such paper."}
        return

    title = row["title"] or ""
    with db.session() as conn:
        settings = db.get_settings(conn)
    email = (settings.get("contact_email") or "").strip()
    s2_key = (settings.get("semantic_scholar_key") or "").strip()

    yield "start", {
        "paper_id": paper_id,
        "title": title,
        "has_pdf": bool(row["pdf_path"]),
        "identifiers": {
            "doi": row["doi"], "arxiv_id": row["arxiv_id"],
            "openalex_id": row["openalex_id"], "pmid": row["pmid"],
        },
    }

    found = _Found()

    # --- routes that need no network call ---------------------------------
    if row["arxiv_id"]:
        cand = found.add(Candidate(
            url=arxiv.pdf_url(row["arxiv_id"]), origin="record", kind="arxiv",
            label="arXiv preprint", note=row["arxiv_id"],
        ))
        if cand:
            yield "candidate", cand.as_dict()
    if row["oa_pdf_url"]:
        cand = found.add(Candidate(
            url=row["oa_pdf_url"], origin="record", kind="record",
            label="Open-access link on record", note="stored when the paper was added",
        ))
        if cand:
            yield "candidate", cand.as_dict()

    # --- ask the sources, in parallel -------------------------------------
    doi = (row["doi"] or "").strip()
    plans: list[tuple[str, str, Any]] = []

    async with make_client() as client:
        if doi and email:
            plans.append(("unpaywall", "Unpaywall", unpaywall.fetch_oa(client, doi, email)))
        elif doi:
            yield "stage", {
                "name": "unpaywall", "label": "Unpaywall", "status": "skipped",
                "detail": "needs a contact email in Settings",
            }
        if doi or row["openalex_id"]:
            call = (
                openalex.fetch_by_id(client, row["openalex_id"], email)
                if row["openalex_id"] else openalex.fetch_by_doi(client, doi, email)
            )
            plans.append(("openalex", "OpenAlex", call))
        ident = f"DOI:{doi}" if doi else (f"arXiv:{row['arxiv_id']}" if row["arxiv_id"] else None)
        if ident:
            plans.append(("s2", "Semantic Scholar", s2.fetch_paper(client, ident, s2_key)))
        if not row["arxiv_id"] and title:
            plans.append(("arxiv_search", "arXiv title search", arxiv.search_by_title(client, title)))

        for name, label, _ in plans:
            yield "stage", {"name": name, "label": label, "status": "running",
                            "detail": "looking…"}

        labels = {name: label for name, label, _ in plans}
        tasks = [asyncio.create_task(_lookup(name, coro)) for name, _, coro in plans]
        for task in asyncio.as_completed(tasks):
            name, data, err = await task
            label = labels[name]
            if err:
                yield "stage", {"name": name, "label": label, "status": "error", "detail": err}
                continue

            new: list[Candidate] = []
            if name == "unpaywall" and data:
                for loc in data.get("oa_locations") or []:
                    host = loc.get("host") or "host unknown"
                    version = loc.get("version") or ""
                    note = " · ".join(x for x in (host, version) if x)
                    if loc.get("pdf_url"):
                        new.append(Candidate(loc["pdf_url"], name, "unpaywall_pdf",
                                             "Open-access file", note))
                    if loc.get("landing_page_url"):
                        new.append(Candidate(loc["landing_page_url"], name, "landing",
                                             "Open-access landing page", note))
                if not (data.get("oa_locations") or []) and data.get("oa_pdf_url"):
                    new.append(Candidate(data["oa_pdf_url"], name, "unpaywall_pdf",
                                         "Open-access file", data.get("oa_status")))
            elif name == "openalex" and data:
                # fetch_by_doi/fetch_by_id already return a parsed work, whose
                # "locations" holds every copy OpenAlex knows of, open or not.
                for loc in data.get("locations") or []:
                    if loc.get("pdf_url"):
                        note = " · ".join(
                            x for x in (loc.get("name"), loc.get("version")) if x
                        )
                        new.append(Candidate(loc["pdf_url"], name, "openalex_pdf",
                                             "Repository copy", note or loc.get("kind")))
                if data.get("oa_pdf_url"):
                    new.append(Candidate(data["oa_pdf_url"], name, "openalex_pdf",
                                         "OpenAlex open-access link", data.get("oa_status")))
            elif name == "s2" and data:
                if data.get("oa_pdf_url"):
                    new.append(Candidate(data["oa_pdf_url"], name, "s2_pdf",
                                         "Semantic Scholar open copy", None))
            elif name == "arxiv_search" and data:
                rejected = 0
                for hit in data:
                    if _title_matches(title, hit.get("title") or ""):
                        new.append(Candidate(hit["pdf_url"], name, "arxiv_guess",
                                             "arXiv copy found by title", hit.get("arxiv_id")))
                        break   # one confident match is enough
                    rejected += 1
                if rejected and not new:
                    # Worth saying: "nothing new" would hide that arXiv had
                    # near misses and they were turned down on purpose.
                    yield "stage", {
                        "name": name, "label": label, "status": "ok",
                        "detail": f"{rejected} near miss{'' if rejected == 1 else 'es'}, "
                                  "none the same paper",
                    }
                    continue

            kept = [c for c in (found.add(c) for c in new) if c]
            yield "stage", {
                "name": name, "label": label, "status": "ok",
                "detail": f"{len(kept)} new link{'' if len(kept) == 1 else 's'}"
                          if kept else "nothing new",
            }
            for cand in kept:
                yield "candidate", cand.as_dict()

        # --- try them ------------------------------------------------------
        queue = found.ordered()
        if not queue:
            yield "done", {
                "ok": False, "paper_id": paper_id, "tried": 0,
                "blocked": [], "attempted": [],
                "message": "No source offered a download link for this paper. "
                           "Use the publisher link, or attach a PDF you already have.",
            }
            return

        yield "trying", {"count": len(queue)}

        # The queue grows while it is walked: a landing page that turns out not
        # to be a file is read for the link to the real one, which goes to the
        # front. Hence an index rather than a for-loop over a fixed list.
        index = 0
        pages_read = 0
        blocked: list[Candidate] = []
        attempted: list[Candidate] = []
        while index < len(queue) and index < MAX_CANDIDATES:
            cand = queue[index]
            yield "attempt", {
                "index": index, "total": len(queue), "url": cand.url,
                "label": cand.label, "note": cand.note, "origin": cand.origin,
            }
            attempted.append(cand)
            got = await _download(client, cand.url)

            if got.body is not None:
                rel = _store(paper_id, got.body, cand.url)
                yield "attempt_result", {"index": index, "ok": True, "detail": got.detail}
                yield "done", {
                    "ok": True, "paper_id": paper_id, "pdf_path": rel,
                    "url": cand.url, "tried": index + 1, "bytes": len(got.body),
                    "message": f"Stored from {cand.label.lower()} ({got.detail}).",
                }
                return

            if got.blocked:
                blocked.append(cand)

            # Not a file. If it was a page, see whether it names one.
            discovered: list[Candidate] = []
            if got.page is not None and pages_read < MAX_PAGES_READ:
                pages_read += 1
                for url, how in _links_in_page(got.page, cand.url):
                    discovered.append(Candidate(url, "page", "page", "Link inside the page", how))
            rewrite = _rewritten(cand.url)
            if rewrite:
                discovered.append(
                    Candidate(rewrite[0], "page", "page", "Guessed file location", rewrite[1])
                )

            kept = [c for c in (found.add(c) for c in discovered) if c]
            yield "attempt_result", {
                "index": index, "ok": False, "detail": got.detail,
                "followed": len(kept), "blocked": got.blocked, "url": cand.url,
            }
            # Straight after the page that named them, ahead of weaker guesses.
            for offset, cand_new in enumerate(kept, start=1):
                queue.insert(index + offset, cand_new)
                yield "candidate", cand_new.as_dict()
            index += 1

    if blocked:
        # Worth separating from a plain failure. These links are not broken and
        # the paper may well be free — they simply refuse a script. Opening one
        # in a browser and saving the file, then Add PDF, takes two clicks, and
        # that is a far better answer than "none returned a usable PDF".
        message = (
            f"Tried {index} link{'' if index == 1 else 's'}. "
            f"{len(blocked)} {'was' if len(blocked) == 1 else 'were'} blocked by a bot check "
            "rather than missing — open one below in your browser, save the PDF, "
            "then use Add PDF."
        )
    else:
        message = (
            f"Tried {index} link{'' if index == 1 else 's'}; none returned a usable PDF."
        )
    yield "done", {
        "ok": False, "paper_id": paper_id, "tried": index,
        "blocked": [c.as_dict() for c in blocked],
        # Every link that was tried, so the panel can offer them all. A search
        # that failed is still a list of places this paper might be, and the
        # browser succeeds at plenty of them that a script cannot.
        "attempted": [c.as_dict() for c in attempted],
        "message": message,
    }
