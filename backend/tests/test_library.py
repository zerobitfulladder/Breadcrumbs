"""The library itself: a fresh install, adding papers, and who their authors are.

Everything here starts from an empty `BREADCRUMBS_HOME`, the state a new user
is in. The owner's own library grew through every migration and can hide a
schema the code has drifted away from; a fresh one cannot. Nothing touches the
network: the source layer is switched off for every test.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import api, db, ingest
from backend.sources import http


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("BREADCRUMBS_HOME", str(tmp_path))
    db.init_db()
    http.set_offline(True)
    c = db.connect()
    yield c
    c.close()
    http.set_offline(False)


def _save(conn: sqlite3.Connection, by_source: dict, references: list | None = None) -> int:
    """Save a paper the way the Add screen does, from what the sources returned."""
    bundle = ingest.Bundle(
        kind="doi", value=by_source["openalex"]["doi"], by_source=by_source, outcomes={},
        merged=ingest.merge_records(by_source), references=references or [],
    )
    result = asyncio.run(ingest.save_bundle(conn, bundle))
    conn.commit()
    return result.paper_id


def _paper(doi: str, title: str, year: int, authors: list[dict] | None = None) -> dict:
    return {"doi": doi, "title": title, "year": year, "authors": authors or []}


# --- a fresh install -----------------------------------------------------------
def test_a_fresh_library_answers_every_page_request(conn):
    client = TestClient(api.app, raise_server_exceptions=False)
    for route in api.app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods and "{" not in route.path:
            assert client.get(route.path).status_code < 500, route.path


def test_a_paper_citing_one_already_held_is_linked_to_it(conn):
    # The core of the app, and what a schema drift broke on every new library:
    # the link was written to columns only an old database still had.
    cited = _save(conn, {"openalex": _paper("10.1000/cited", "The cited paper", 2010)})
    citing = _save(
        conn,
        {"openalex": _paper("10.1000/citing", "The citing paper", 2020)},
        references=[{"doi": "10.1000/cited", "title": "The cited paper", "year": 2010}],
    )
    links = TestClient(api.app).get("/api/timeline").json()["links"]
    assert [(l["src_paper_id"], l["dst_paper_id"], l["type"]) for l in links] == [
        (citing, cited, "cites"),
    ]


def test_a_reference_waits_until_its_paper_is_added(conn):
    citing = _save(
        conn,
        {"openalex": _paper("10.1000/citing", "The citing paper", 2020)},
        references=[{"doi": "10.1000/later", "title": "Added later", "year": 2015}],
    )
    assert TestClient(api.app).get("/api/timeline").json()["links"] == []
    later = _save(conn, {"openalex": _paper("10.1000/later", "Added later", 2015)})
    links = TestClient(api.app).get("/api/timeline").json()["links"]
    assert [(l["src_paper_id"], l["dst_paper_id"]) for l in links] == [(citing, later)]


# --- authors ---------------------------------------------------------------------
def _authors(*names_and_ids: tuple[str, str]) -> list[dict]:
    key = "openalex_id" if names_and_ids[0][1].startswith("A") else "s2_author_id"
    return [{"name": n, key: i, "position": p} for p, (n, i) in enumerate(names_and_ids)]


def test_co_authors_sharing_a_surname_stay_two_people(conn):
    # OpenAlex and Semantic Scholar spell the same two people differently.
    # Grafting S2 ids on by surname gave both the first Wang's id, and the
    # store then filed Hao Wang under Yi Wang's row and dropped him.
    paper_id = _save(conn, {
        "openalex": _paper("10.1000/wang", "Two Wangs", 2021,
                           _authors(("Yi Wang", "A1"), ("H. Wang", "A2"))),
        "s2": {"authors": _authors(("Y. Wang", "X1"), ("Hao Wang", "X2"))},
    })
    rows = conn.execute(
        "SELECT a.name, a.openalex_id, a.s2_author_id FROM paper_authors pa "
        "JOIN authors a ON a.id = pa.author_id WHERE pa.paper_id = ? ORDER BY pa.position",
        (paper_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [("Yi Wang", "A1", "X1"), ("H. Wang", "A2", "X2")]


def test_an_ambiguous_name_gets_no_borrowed_id():
    # Each "Y. Wang" could be either of S2's Wangs, so neither id is a safe guess.
    merged = ingest._merge_authors({
        "openalex": {"authors": _authors(("Y. Wang", "A1"), ("Y. Wang", "A2"))},
        "s2": {"authors": _authors(("Yi Wang", "X1"), ("Yu Wang", "X2"))},
    })
    assert [a.get("openalex_id") for a in merged] == ["A1", "A2"]
    assert [a.get("s2_author_id") for a in merged] == [None, None]


def test_names_are_paired_exactly_first_then_by_initials():
    merged = ingest._merge_authors({
        "openalex": {"authors": _authors(
            ("Yi Wang", "A1"), ("Y. Wang", "A2"), ("T. Lillicrap", "A3"),
        )},
        "s2": {"authors": _authors(
            ("Yu Wang", "X1"), ("Yi Wang", "X2"), ("Timothy P. Lillicrap", "X3"),
        )},
    })
    # Yi Wang takes the exact match, which leaves Yu Wang the only reading of
    # "Y. Wang"; the initial T. stands for Timothy.
    assert [a.get("s2_author_id") for a in merged] == ["X2", "X1", "X3"]


def test_an_authors_affiliation_follows_their_latest_paper(conn):
    # Stored once, it kept whichever paper arrived first: LeCun at AT&T for good.
    def lecun(institution: str) -> list[dict]:
        return [{"name": "Yann LeCun", "openalex_id": "A1", "position": 0,
                 "institutions": [{"name": institution, "openalex_id": institution}]}]

    _save(conn, {"openalex": _paper("10.1000/old", "Older", 1998, lecun("AT&T"))})
    _save(conn, {"openalex": _paper("10.1000/new", "Newer", 2010, lecun("NYU"))})
    author_id = conn.execute("SELECT id FROM authors").fetchone()["id"]
    assert TestClient(api.app).get(f"/api/authors/{author_id}").json()["affiliation"] == "NYU"


# --- what Bread writes -------------------------------------------------------------
def test_bread_can_draw_a_typed_link(conn):
    from backend.ai import tools

    a = _save(conn, {"openalex": _paper("10.1000/a", "Method", 2015)})
    b = _save(conn, {"openalex": _paper("10.1000/b", "Its extension", 2018)})
    out = tools.create_link(conn, b, a, "extends", note="Adds a second layer.")
    assert out.get("ok"), out
    row = conn.execute("SELECT type, origin, confirmed FROM links").fetchone()
    assert tuple(row) == ("extends", "assistant", 0)
    assert "error" in tools.create_link(conn, b, a, "inspired")


def test_merging_authors_who_both_have_a_homepage_keeps_the_survivors(conn):
    from backend import store
    from backend.ai import tools

    _save(conn, {"openalex": _paper("10.1000/h1", "One", 2001,
                                    _authors(("Geoffrey Hinton", "A1")))})
    _save(conn, {"openalex": _paper("10.1000/h2", "Two", 2002,
                                    _authors(("Geoffrey E. Hinton", "A2")))})
    keep, absorb = [r["id"] for r in conn.execute("SELECT id FROM authors ORDER BY id")]
    tools.set_author_homepage(conn, keep, "https://example.org/keep")
    tools.set_author_homepage(conn, absorb, "https://example.org/absorbed")
    store.merge_authors(conn, keep, absorb)
    assert [tuple(r) for r in conn.execute("SELECT author_id, url FROM author_links")] == [
        (keep, "https://example.org/keep"),
    ]
