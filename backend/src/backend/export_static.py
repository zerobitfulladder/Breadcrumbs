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

Responses come from the real application rather than from SQL written a second
time here, so the files cannot drift from what the API would have returned:

    uv run --project backend python -m backend.export_static --out site/data
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from . import db
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

    if refused:
        hosts = sorted({r.split(":", 1)[0] for r in refused})
        print(f"  {len(refused)} request(s) not made ({', '.join(hosts)}); stored data used")
    if "YOUR-USERNAME" in config["repo_url"]:
        print("  ! pages.json still has the placeholder repo_url; set it before publishing")
    print(f"  4 files, {total / 1024:.0f} KB")


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
