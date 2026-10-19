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
