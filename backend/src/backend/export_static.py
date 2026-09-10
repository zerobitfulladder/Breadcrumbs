"""Freeze the library into static JSON, for publishing the reader without a backend.

The site this produces is your library as a read-only page: papers, their
fields and notes, authors, references and the links between them. It needs no
server, so it can sit on GitHub Pages.

Two things are deliberately left out.

PDFs, because a paywalled article is the publisher's to distribute, not yours —
what you may share is the record and your own writing about it. And everything
in `settings`, because that table holds your API keys; the conversations go too,
for the same reason. Only the routes listed in ROUTES are exported, so nothing
is published by having been forgotten.

Every image the pages show is written into the export as a file, portraits from
Wikipedia included. A published library should not have to reach across the
internet to draw itself, and one that hotlinks somebody else's server both
leaks who is reading it and breaks the day that server stops allowing it. This
is the one part of a build that goes out to the network, and it goes out once
per portrait; failing to reach one leaves that author pointing at the original
URL, which is where it started.

Responses come from the real application rather than from SQL written a second
time here, so the files cannot drift from what the API would have returned:

    uv run --project backend python -m backend.export_static --out site/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import httpx

from fastapi.testclient import TestClient

from . import db, photos
from .sources import http

#: Library-wide documents, fetched once and keyed into library.json by name.
ROUTES: tuple[tuple[str, str], ...] = (
    ("/api/timeline", "timeline.json"),
    ("/api/shelves", "shelves.json"),
    ("/api/authors", "authorList.json"),
    ("/api/stats", "stats.json"),
    ("/api/map?scope=library", "map.json"),
)

#: An author's wider output — everything they have written, not only what the
#: library holds — is fetched live, twice per author with no cache, so it is
#: left out unless `--with-works` asks for it and allows the build a network.
#: This stands in, in the shape the endpoint already uses for an author it
#: cannot list, so the panel explains itself instead of showing nothing.
WORKS_OMITTED = {
    "works": [],
    "profile": None,
    "detail": "Published copies do not include an author's wider output.",
}

#: Which extra documents an author gets is decided in export(): /profile and
#: /facts search live when the library holds no biography, so they are written
#: only for authors that already have one.


#: Defaults for pages.json, used for anything it leaves blank or omits.
SITE_DEFAULTS: dict[str, str] = {
    "repo_url": "https://github.com/YOUR-USERNAME/Breadcrumbs",
    "owner": "",
    "title": "",
    "intro": "",
}


def site_config(root: Path) -> dict[str, str]:
    """Read pages.json, the published copy's own settings.

    Kept out of the components so the link, the title and the blurb can be
    changed without touching TypeScript or rebuilding: it is exported as data
    and read at runtime, like everything else on the page.

    A missing or unreadable file is not an error — the defaults describe an
    unconfigured copy, which is exactly what one is until this is filled in.
    """
    path = root / "pages.json"
    config = dict(SITE_DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    for key in SITE_DEFAULTS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            config[key] = value.strip()
    return config


def strip_local(doc: Any) -> Any:
    """Remove what points at files this site will not carry.

    `pdf_path` is what the reader checks before offering the PDF, so clearing
    it makes the buttons disappear on their own rather than needing the
    components to know they are running without a backend.
    """
    if isinstance(doc, dict):
        out = {}
        for key, value in doc.items():
            if key in ("pdf_path", "note_path"):
                out[key] = None
            elif key == "has_pdf":
                out[key] = 0
            else:
                out[key] = strip_local(value)
        return out
    if isinstance(doc, list):
        return [strip_local(v) for v in doc]
    return doc


def write(out: Path, name: str, doc: Any) -> int:
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(strip_local(doc), ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


#: A portrait is a picture of a person, not an archive master. Anything past
#: this is left where it is rather than dragged into the export.
MAX_PORTRAIT_BYTES = 8 * 1024 * 1024
#: Between portrait requests. Wikimedia is being asked for a favour here.
PORTRAIT_PAUSE = 0.6


def _portrait_cache() -> Path:
    """Where fetched portraits are kept between builds.

    Beside the ones you supplied, under the library folder rather than in the
    export, because the export is thrown away and rebuilt each time and this is
    the thing worth not doing twice. Cleared by wiping the library, like
    everything else in there.
    """
    return db.library_root() / "authors" / "cache"


#: The longest this will wait on one picture before giving it up. A build that
#: sits for ten minutes is worse than a build that reuses last time's file.
MAX_PORTRAIT_WAIT = 20.0


def _fetch(client: httpx.Client, url: str) -> bytes | None:
    """One picture, with room for a host that would rather we slowed down.

    A 429 or a 5xx is the server asking for a moment, not a refusal, so it is
    waited out and tried again; anything else, including a 404, is an answer.

    When the host says how long — Wikimedia answers a rate limit with
    Retry-After, and it can be ten minutes — that is taken at its word rather
    than guessed at. Ten minutes is not a wait, it is a different build, so a
    number past what this is willing to sit through ends the attempt then and
    there instead of sleeping through three doomed retries. Whatever the reason,
    coming back empty is survivable: the caller falls back to a picture it
    already has.
    """
    waited = 0.0
    for attempt in range(3):
        try:
            resp = client.get(url)
        except httpx.HTTPError:
            return None
        if resp.status_code < 400:
            return resp.content
        if resp.status_code != 429 and resp.status_code < 500:
            return None
        # 2s, then 6, then 12, unless the host names its own figure.
        pause = 2.0 * (attempt + 1) ** 1.6
        told = resp.headers.get("retry-after", "")
        if told.isdigit():
            pause = float(told)
        if waited + pause > MAX_PORTRAIT_WAIT:
            return None
        time.sleep(pause)
        waited += pause
    return None


class _Portraits:
    """What the portrait pass produced, for the summary and the rewrite."""

    def __init__(self) -> None:
        #: Remote or route URL -> path inside the export, relative to the data
        #: directory. Keyed by URL so one picture used in three places is
        #: fetched once and rewritten everywhere.
        self.paths: dict[str, str] = {}
        self.count = 0
        self.bytes = 0
        self.failed = 0
        #: Taken from the cache rather than the network.
        self.reused = 0


def _relocate(doc: Any, paths: dict[str, str]) -> Any:
    """Swap every URL that was brought local for the file it became."""
    if isinstance(doc, dict):
        return {k: _relocate(v, paths) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_relocate(v, paths) for v in doc]
    if isinstance(doc, str):
        return paths.get(doc, doc)
    return doc


def _author_rows(doc: Any) -> Iterator[dict[str, Any]]:
    """Every author row in a bundle, wherever it happens to sit."""
    if isinstance(doc, dict):
        if isinstance(doc.get("id"), int) and "thumbnail_url" in doc:
            yield doc
        for value in doc.values():
            yield from _author_rows(value)
    elif isinstance(doc, list):
        for value in doc:
            yield from _author_rows(value)


def _write_portraits(
    out_dir: Path, authors: dict[str, Any], papers: dict[str, Any]
) -> _Portraits:
    """Bring every author's picture into the export as a file.

    Resolved per author rather than per URL, because the goal is that no
    portrait is left pointing outward, not that every URL is fetched. An author
    has up to two: the thumbnail beside their name and the full image the
    lightbox opens. If the full one cannot be had — refused, too large, not an
    image — the thumbnail stands in for both, which is a smaller picture rather
    than a request to somebody else's server.

    Files are named for a digest of the URL they came from, so two authors
    sharing a picture share the file, and a picture that changes upstream lands
    under a new name rather than being served stale from a cache.
    """
    out = _Portraits()
    target_dir = out_dir / "photos"
    #: URL -> path in the export, or None where it could not be brought over.
    seen: dict[str, str | None] = {}
    #: Author id -> the small picture that stands for them, for the bylines.
    small_for: dict[int, str] = {}

    def keep(name: str, data: bytes) -> str:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_bytes(data)
        out.count += 1
        out.bytes += len(data)
        return f"photos/{name}"

    def remote(url: Any) -> bool:
        return isinstance(url, str) and url.startswith("http")

    def bring(client: httpx.Client, url: str) -> str | None:
        if url in seen:
            return seen[url]
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]

        # Already held from an earlier build. A picture fetched once is a
        # picture fetched: the file does not change, and asking again is how a
        # second build in the same ten minutes talks its way into a rate limit
        # and comes back with less than the first one did.
        rel = None
        for held in _portrait_cache().glob(f"{digest}.*"):
            rel = keep(f"wiki-{digest}{held.suffix}", held.read_bytes())
            out.reused += 1
            break

        if rel is None:
            # Paced. Asked for twenty pictures as fast as the socket allows,
            # Wikimedia starts answering 429 around the sixth.
            if seen:
                time.sleep(PORTRAIT_PAUSE)
            data = _fetch(client, url)
            if data is not None:
                # The bytes decide the format; a Content-Type is a claim.
                ext = photos.sniff(data)
                if ext is not None and len(data) <= MAX_PORTRAIT_BYTES:
                    _portrait_cache().mkdir(parents=True, exist_ok=True)
                    (_portrait_cache() / f"{digest}.{ext}").write_bytes(data)
                    rel = keep(f"wiki-{digest}.{ext}", data)

        seen[url] = rel
        if rel is None:
            out.failed += 1
        else:
            out.paths[url] = rel
        return rel

    client = httpx.Client(
        # Redirects because Wikimedia issues them, and the User-Agent the rest
        # of the app uses because Wikimedia answers 403 to a client that does
        # not name itself.
        follow_redirects=True,
        timeout=45.0,
        headers={"User-Agent": http.USER_AGENT},
    )
    with db.session() as conn, client:
        for key, entry in authors.items():
            profile = entry.get("profile")
            if not isinstance(profile, dict):
                continue

            stored = photos.stored_path(conn, int(key))
            if stored is not None:
                # The portrait you chose stands for this author everywhere,
                # the byline chips included, which would otherwise still show
                # whatever Wikipedia had.
                rel = keep(f"{key}{stored.suffix}", stored.read_bytes())
                for field in ("portrait_url", "portrait_full_url",
                              "thumbnail_url", "image_url"):
                    if profile.get(field):
                        profile[field] = rel
                small_for[int(key)] = rel
                continue

            small = profile.get("thumbnail_url") or profile.get("portrait_url")
            large = profile.get("image_url") or profile.get("portrait_full_url")
            thumb = bring(client, small) if remote(small) else None
            if remote(large) and large != small:
                full = bring(client, large) or thumb
            else:
                full = thumb
            if thumb is None and full is None:
                continue
            small_for[int(key)] = thumb or full or ""

            for field, value in (
                ("thumbnail_url", thumb or full),
                ("portrait_url", thumb or full),
                ("image_url", full or thumb),
                ("portrait_full_url", full or thumb),
            ):
                if profile.get(field):
                    profile[field] = value

        # The byline chips, which carry their own copy of the thumbnail and not
        # always the same one: an author with a portrait you supplied still had
        # Wikipedia's on every paper they wrote, and the two URLs can differ by
        # host, so this goes by author rather than by URL. An author with no
        # profile of their own is never reached by the pass above and is
        # fetched here or nowhere.
        for row in _author_rows(papers):
            url = row.get("thumbnail_url")
            local = small_for.get(row["id"])
            if local:
                row["thumbnail_url"] = local
            elif remote(url):
                brought = bring(client, url)
                if brought:
                    row["thumbnail_url"] = brought
                    small_for[row["id"]] = brought

    return out


def export(out_dir: Path, with_works: bool = False) -> None:
    """Write the library as three bundles plus the page's own settings.

    One file per endpoint would be 1,300 of them for a library this size, and
    the reader would then make a request per click. Bundled, the whole library
    is three files a static host can compress and cache, and a paper opens
    from memory.

    The split is by when things are needed. `library.json` is what the first
    screen draws and is loaded immediately; `papers.json` and `authors.json`
    are fetched the first time something is opened, and not at all by a
    visitor who only looks at the timeline.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    from .api import app

    # Every export is over a thousand calls, and httpx logs each one. That is
    # a thousand lines saying the app answered its own request, which buries
    # the two lines that matter — what was written, and what could not be.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Nothing leaves the machine unless the wider-output listing was asked for,
    # which cannot be answered from the library at all. Everything else has a
    # stored form: /facts keeps the Wikipedia half it already has and drops the
    # ORCID and OpenAlex halves it would otherwise re-fetch per author, and the
    # map uses the coordinates it holds rather than geocoding what it lacks.
    http.set_offline(not with_works)

    with TestClient(app) as client:
        def get(url: str) -> Any:
            resp = client.get(url)
            return resp.json() if resp.status_code == 200 else None

        library: dict[str, Any] = {name.removesuffix(".json").replace("/", "_"): get(url)
                                   for url, name in ROUTES}

        with db.session() as conn:
            paper_ids = [r["id"] for r in conn.execute("SELECT id FROM papers ORDER BY id")]
            author_ids = [r["id"] for r in conn.execute("SELECT id FROM authors ORDER BY id")]
            profiled = {
                r["author_id"]
                for r in conn.execute("SELECT author_id FROM author_profiles")
            }

        print(f"  {len(paper_ids)} papers, {len(author_ids)} authors, {len(profiled)} biographies")

        papers: dict[str, Any] = {}
        for n, pid in enumerate(paper_ids, 1):
            papers[str(pid)] = {
                "paper": get(f"/api/papers/{pid}"),
                "references": get(f"/api/papers/{pid}/references"),
                "cited_by": get(f"/api/papers/{pid}/cited-by"),
                "highlights": get(f"/api/papers/{pid}/highlights"),
            }
            if n % 25 == 0 or n == len(paper_ids):
                print(f"    papers {n}/{len(paper_ids)}", flush=True)

        authors: dict[str, Any] = {}
        for n, aid in enumerate(author_ids, 1):
            entry: dict[str, Any] = {
                "author": get(f"/api/authors/{aid}"),
                "links": get(f"/api/authors/{aid}/links"),
                "works": get(f"/api/authors/{aid}/works?limit=200") if with_works
                         else dict(WORKS_OMITTED),
            }
            # Only for authors the library already has a biography for: the
            # endpoints behind these search live when it does not.
            if aid in profiled:
                entry["profile"] = get(f"/api/authors/{aid}/profile")
                entry["facts"] = get(f"/api/authors/{aid}/facts")
            authors[str(aid)] = entry
            if n % 50 == 0 or n == len(author_ids):
                print(f"    authors {n}/{len(author_ids)}", flush=True)

    refused = http.set_offline(False)

    # Enough of each paper to search and list without loading the bundle.
    library["index"] = [
        {
            "id": p["id"],
            "title": p.get("title"),
            "year": p.get("year"),
            "authors": p.get("authors") or [],
            "venue": p.get("venue"),
        }
        for p in (library.get("timeline") or {}).get("papers", [])
    ]

    # --- portraits ----------------------------------------------------------
    #
    # Two kinds arrive here and neither survives a static copy untouched.
    #
    # One you supplied is a file in the library folder served by a route, and a
    # route is exactly what a copy with no backend cannot answer: those authors
    # showed an empty frame with a request for /api/authors/38/photo behind it.
    # It is copied in beside the JSON.
    #
    # One from Wikipedia is a URL on Wikipedia, which does load — by reaching
    # out from the reader's browser to somebody else's server every time the
    # page is opened. It is fetched once, here, and written in as a file too.
    #
    # Either way the URL is rewritten relative to the data directory rather
    # than to the page, because the page can be published at a path this cannot
    # know; the client joins the two ends. Unlike a PDF these are publishable:
    # a portrait of the author of a paper you hold.
    portraits = _write_portraits(out_dir, authors, papers)
    if portraits.paths:
        # Rewritten by URL rather than by field, so the same picture is caught
        # wherever it appears — an author's panel, and the byline chip on every
        # paper they wrote.
        library = _relocate(library, portraits.paths)
        papers = _relocate(papers, portraits.paths)
        authors = _relocate(authors, portraits.paths)

    root = Path(__file__).resolve().parents[3]
    config = site_config(root)

    total = 0
    for name, doc in (
        ("library.json", library),
        ("papers.json", papers),
        ("authors.json", authors),
        ("site.json", config),
    ):
        size = write(out_dir, name, doc)
        total += size
        print(f"  {name:<14} {size / 1024:>7.0f} KB")

    if portraits.count:
        # A failure here is a picture that could not be fetched, not one left
        # pointing outward: an author falls back to another picture of their
        # own, and only an author with no picture at all keeps a remote URL.
        print(f"  {'photos/':<14} {portraits.bytes / 1024:>7.0f} KB  "
              f"({portraits.count} portrait{'' if portraits.count == 1 else 's'}"
              f"{f', {portraits.reused} from cache' if portraits.reused else ''}"
              f"{f', {portraits.failed} not fetched' if portraits.failed else ''})")
        total += portraits.bytes

    if refused:
        hosts = sorted({r.split(":", 1)[0] for r in refused})
        print(f"  {len(refused)} request(s) not made ({', '.join(hosts)}); stored data used")
    if "YOUR-USERNAME" in config["repo_url"]:
        print("  ! pages.json still has the placeholder repo_url; set it before publishing")
    print(f"  {4 + portraits.count} files, {total / 1024:.0f} KB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="site/data", help="directory to write (recreated)")
    ap.add_argument(
        "--with-works",
        action="store_true",
        help="also fetch each author's full output from OpenAlex (slow: minutes)",
    )
    args = ap.parse_args()
    db.init_db()
    out = Path(args.out)
    print(f"exporting to {out}")
    export(out, args.with_works)


if __name__ == "__main__":
    main()
