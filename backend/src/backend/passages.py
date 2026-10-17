"""Passage search over the full text of every stored PDF.

The library's own search covers titles and abstracts. That answers "which
paper is this", but not "which of my papers discusses eligibility traces", or
"where in this paper do they say how the learning rate was chosen", when the
words the user has in mind are not the ones the paper uses.

Two retrievers run over the same passages and their rankings are fused:

  * keyword: SQLite FTS5 with BM25. Exact on names, symbols and rare terms
    ("SoftHebb", "Fisher", "1.36%"), which embeddings blur.
  * semantic: a small local embedding model (bge-small-en-v1.5 through
    fastembed, ONNX on the CPU). Finds a passage that says the same thing in
    other words, which keywords miss.

Reciprocal rank fusion combines them by rank alone, so neither score needs
calibrating against the other. The vectors are searched by brute force in
numpy: a personal library is thousands of passages, which a single matrix
product covers in milliseconds, and an approximate index would add a
dependency to save time nobody would notice.

Everything here is derived from the PDF and rebuilt when the file's hash
changes, so it is safe to delete. Embedding stays on this machine; the model is
downloaded once into the user's cache and nothing is sent anywhere.

    python -m backend.passages              index what is missing or stale
    python -m backend.passages --rebuild    re-index everything
    python -m backend.passages --dry-run    say what would be indexed
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import db, pdftext

log = logging.getLogger(__name__)

#: Names the rule that cut the passages. Changing the rule means changing this,
#: which marks every stored passage stale.
CHUNKER = "blocks-1100/v2"
EMBEDDER = "BAAI/bge-small-en-v1.5"
#: BGE models are trained with this instruction on the query side only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

TARGET_CHARS = 1100
MIN_CHARS = 200
#: Candidates each retriever contributes before fusion.
POOL = 50
#: The usual RRF constant. Large enough that rank 1 does not swamp the rest.
RRF_K = 60

_REFERENCES = re.compile(
    r"^(?:\d+\.?\s*|[ivx]+\.?\s*)?(references|bibliography|literature cited|references and notes)$",
    re.I,
)
_BACK_MATTER = re.compile(
    r"^(?:[a-z]\.?\s*|\d+\.?\s*)?(appendix|appendices|supplementary|supplemental|methods)\b", re.I
)
#: A numbered section heading: "4 Methods", "4.2 Atari experiments", "A.1 Proofs".
#: Seen after a bibliography, it means the paper has resumed.
_SECTION = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)+)\.?\s+[A-Z][\w\- ,:]{2,40}$")
_YEAR = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b")


def looks_like_bibliography(text: str) -> bool:
    """Dense with years, as a run of reference entries is.

    Prose citing in author–year style has a few; a reference list has one per
    couple of hundred characters.
    """
    return len(_YEAR.findall(text)) >= max(3, len(text) // 260)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


# ---------------------------------------------------------------------------
# cutting a PDF into passages
# ---------------------------------------------------------------------------
@dataclass
class Passage:
    page: int
    seq: int
    text: str
    references_section: bool


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", pdftext.join_broken_words(text)).strip()


def _split_long(text: str) -> list[str]:
    """A block far over the target, cut at sentence ends."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(text):
        if current and len(current) + len(sentence) > TARGET_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_pdf(path: Path) -> list[Passage]:
    """Cut a PDF into passages of about a paragraph or two.

    Built from the text blocks PyMuPDF reports, which follow the typesetter's
    paragraphs and columns, so a passage rarely starts mid-thought. Blocks are
    gathered until a passage reaches the target length, and a passage never
    continues onto the next page: every hit can then name one page to cite.

    The bibliography is kept but marked. A reference list is dense with exactly
    the words a query uses ("Hebbian learning in deep networks") while saying
    nothing about them, so search leaves it out unless asked.
    """
    import pymupdf

    out: list[Passage] = []
    in_refs = False
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            if number > pdftext.MAX_PAGES_SCANNED:
                break
            current: list[str] = []
            current_refs = in_refs

            def flush() -> None:
                nonlocal in_refs, current_refs
                text = " ".join(current).strip()
                if not text:
                    return
                # After a reference list, papers often resume (methods,
                # appendices) without a heading this recognises. Text that
                # does not read like references ends the bibliography.
                if current_refs and not looks_like_bibliography(text):
                    in_refs = current_refs = False
                for piece in _split_long(text) if len(text) > TARGET_CHARS * 1.6 else [text]:
                    # A scrap at a page's end joins the passage before it,
                    # rather than standing alone as a near-empty hit.
                    if (
                        len(piece) < MIN_CHARS and out and out[-1].page == number
                        and out[-1].references_section == current_refs
                    ):
                        out[-1].text = f"{out[-1].text} {piece}"
                    else:
                        out.append(Passage(number, len(out), piece, current_refs))
                current.clear()

            for block in page.get_text("blocks"):
                if block[6] != 0:          # an image, not text
                    continue
                text = _clean(block[4])
                if len(text) < 15 and not _REFERENCES.match(text):
                    continue                # page numbers, stray labels
                heading = text if len(text) < 48 else ""
                if heading and (
                    _REFERENCES.match(heading) or _BACK_MATTER.match(heading)
                    or (in_refs and _SECTION.match(heading))
                ):
                    flush()
                    in_refs = bool(_REFERENCES.match(heading))
                    current_refs = in_refs
                    if not in_refs:
                        current.append(text)
                    continue
                current.append(text)
                if sum(len(t) for t in current) >= TARGET_CHARS:
                    flush()
            flush()
    return out


# ---------------------------------------------------------------------------
# the embedding model
# ---------------------------------------------------------------------------
class EmbedderUnavailable(RuntimeError):
    """The model could not be loaded: offline on first use, or turned off."""


_model: Any = None
_model_error: str | None = None
_model_lock = threading.Lock()


def model_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "breadcrumbs" / "models"


def embedder_name(conn: sqlite3.Connection) -> str | None:
    """The embedding model in use, or None when semantic search is off."""
    name = db.get_setting(conn, "passage_embedder", EMBEDDER).strip()
    return None if name.lower() in ("off", "none", "") else name


def _load(name: str) -> Any:
    global _model, _model_error
    with _model_lock:
        if _model is not None and getattr(_model, "_breadcrumbs_name", None) == name:
            return _model
        try:
            from fastembed import TextEmbedding

            # fastembed caches in the system temp directory by default, which
            # is emptied on reboot: the model would be downloaded again.
            model = TextEmbedding(name, cache_dir=str(model_cache()))
        except Exception as exc:          # network, disk, an unknown name
            _model_error = f"{type(exc).__name__}: {exc}"
            raise EmbedderUnavailable(_model_error) from exc
        model._breadcrumbs_name = name
        _model, _model_error = model, None
        return model


def _unit(rows: Any) -> Any:
    import numpy as np

    matrix = np.asarray(rows, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def embed_passages(name: str, texts: list[str]) -> Any:
    model = _load(name)
    with _model_lock:
        return _unit(list(model.embed(texts, batch_size=32)))


def embed_query(name: str, query: str) -> Any:
    model = _load(name)
    prefix = QUERY_PREFIX if "bge" in name.lower() else ""
    with _model_lock:
        return _unit(list(model.embed([prefix + query])))[0]


# ---------------------------------------------------------------------------
# indexing
# ---------------------------------------------------------------------------
_paper_locks: dict[int, threading.Lock] = {}
_paper_locks_guard = threading.Lock()


def _lock_for(paper_id: int) -> threading.Lock:
    with _paper_locks_guard:
        return _paper_locks.setdefault(paper_id, threading.Lock())


def is_current(conn: sqlite3.Connection, paper_id: int) -> bool:
    path = pdftext.pdf_path(conn, paper_id)
    if path is None:
        return True                       # nothing to index
    row = conn.execute(
        "SELECT pdf_sha256, chunker, embedder FROM passage_index WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        return False
    wanted = embedder_name(conn)
    return (
        row["pdf_sha256"] == pdftext.file_sha256(path)
        and row["chunker"] == CHUNKER
        and (wanted is None or row["embedder"] == wanted)
    )


def index_paper(conn: sqlite3.Connection, paper_id: int, force: bool = False) -> dict[str, Any]:
    """Cut one paper's PDF into passages and embed them, unless already current.

    Commits on its own: the passages are derived data, and a paper indexed
    before a later failure is still worth keeping.
    """
    with _lock_for(paper_id):
        path = pdftext.pdf_path(conn, paper_id)
        if path is None:
            conn.execute("DELETE FROM passages WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM passage_index WHERE paper_id = ?", (paper_id,))
            conn.commit()
            return {"paper_id": paper_id, "status": "no_pdf"}
        if not force and is_current(conn, paper_id):
            return {"paper_id": paper_id, "status": "current"}

        started = time.monotonic()
        chunks = chunk_pdf(path)
        name = embedder_name(conn)
        vectors = None
        note = None
        if name and chunks:
            try:
                vectors = embed_passages(name, [c.text for c in chunks])
            except EmbedderUnavailable as exc:
                # Keyword search still works; the passages are re-embedded the
                # next time the model can be loaded, since the index records
                # that they were made without it.
                note = f"keyword only: {exc}"
                name = None

        with conn:
            conn.execute("DELETE FROM passages WHERE paper_id = ?", (paper_id,))
            conn.executemany(
                "INSERT INTO passages(paper_id, page, seq, text, references_section, embedding) "
                "VALUES(?,?,?,?,?,?)",
                [
                    (
                        paper_id, c.page, c.seq, c.text, int(c.references_section),
                        vectors[i].tobytes() if vectors is not None else None,
                    )
                    for i, c in enumerate(chunks)
                ],
            )
            conn.execute(
                "INSERT INTO passage_index(paper_id, pdf_sha256, chunker, embedder, passages) "
                "VALUES(?,?,?,?,?) ON CONFLICT(paper_id) DO UPDATE SET "
                "pdf_sha256 = excluded.pdf_sha256, chunker = excluded.chunker, "
                "embedder = excluded.embedder, passages = excluded.passages, "
                "indexed_at = datetime('now')",
                (paper_id, pdftext.file_sha256(path), CHUNKER, name, len(chunks)),
            )
        _invalidate()
        out = {
            "paper_id": paper_id, "status": "indexed", "passages": len(chunks),
            "embedder": name, "seconds": round(time.monotonic() - started, 2),
        }
        if note:
            out["note"] = note
        return out


def stale_papers(conn: sqlite3.Connection, paper_ids: Iterable[int] | None = None) -> list[int]:
    if paper_ids is None:
        paper_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM papers WHERE pdf_path IS NOT NULL ORDER BY id"
            )
        ]
    return [pid for pid in paper_ids if not is_current(conn, pid)]


def ensure_indexed(
    conn: sqlite3.Connection, paper_ids: Iterable[int] | None, budget_s: float
) -> list[int]:
    """Index what is stale, within a time budget. Returns what is still left.

    Called from inside an assistant turn, where a minute spent embedding the
    whole library would look like a hang. One paper takes a few seconds; the
    rest is left to the background worker, and the answer says what was not
    searched.
    """
    started = time.monotonic()
    left = stale_papers(conn, paper_ids)
    while left and time.monotonic() - started < budget_s:
        index_paper(conn, left.pop(0))
    return left


# ---------------------------------------------------------------------------
# background indexing
# ---------------------------------------------------------------------------
_queue: "queue.Queue[int | None]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_guard = threading.Lock()


def _work() -> None:
    while True:
        paper_id = _queue.get()
        try:
            conn = db.connect()
            try:
                ids = stale_papers(conn) if paper_id is None else [paper_id]
                for pid in ids:
                    result = index_paper(conn, pid)
                    if result["status"] == "indexed":
                        log.info("indexed paper %s: %s passages", pid, result["passages"])
            finally:
                conn.close()
        except Exception:
            log.exception("passage indexing failed")
        finally:
            _queue.task_done()


def schedule(paper_id: int | None = None) -> None:
    """Index a paper, or everything stale when None, on a background thread."""
    global _worker
    with _worker_guard:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="passage-index", daemon=True)
            _worker.start()
    _queue.put(paper_id)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
_STOP = frozenset(
    "a an and are as at be by can could did do does for from had has have how i if in into "
    "is it its me my of on or our over paper papers so such than that the their them then "
    "there these they this those to use used using was we were what when where which while "
    "who why will with within without would you your about any between does done".split()
)

_matrix: dict[str, Any] = {}
_matrix_guard = threading.Lock()


def _invalidate() -> None:
    with _matrix_guard:
        _matrix.clear()


def _vectors(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every stored embedding as one matrix, reloaded only when the index changes."""
    import numpy as np

    key = tuple(conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM passages WHERE embedding IS NOT NULL"
    ).fetchone())
    with _matrix_guard:
        if _matrix.get("key") == key:
            return _matrix
        rows = conn.execute(
            "SELECT id, paper_id, references_section, embedding FROM passages "
            "WHERE embedding IS NOT NULL ORDER BY id"
        ).fetchall()
        _matrix.clear()
        _matrix["key"] = key
        _matrix["ids"] = np.array([r["id"] for r in rows], dtype=np.int64)
        _matrix["paper"] = np.array([r["paper_id"] for r in rows], dtype=np.int64)
        _matrix["refs"] = np.array([r["references_section"] for r in rows], dtype=bool)
        _matrix["m"] = (
            np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32)
            .reshape(len(rows), -1) if rows else np.zeros((0, 0), dtype=np.float32)
        )
        return _matrix


def fts_query(text: str) -> str | None:
    """A question as an FTS5 query: its content words, any of which may match.

    FTS5 joins bare terms with AND, so a whole question matches almost nothing.
    OR over the words that carry meaning lets BM25 do the ranking, which is
    what it is for. Each term is quoted so FTS syntax in the question is inert.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in _STOP]
    seen = list(dict.fromkeys(words))
    return " OR ".join(f'"{w}"' for w in seen) if seen else None


def _keyword(
    conn: sqlite3.Connection, query: str, paper_id: int | None, with_refs: bool
) -> list[int]:
    match = fts_query(query)
    if not match:
        return []
    rows = conn.execute(
        "SELECT p.id FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
        "WHERE passages_fts MATCH ? AND (? IS NULL OR p.paper_id = ?) "
        "AND (? OR p.references_section = 0) "
        "ORDER BY bm25(passages_fts) LIMIT ?",
        (match, paper_id, paper_id, int(with_refs), POOL),
    ).fetchall()
    return [r["id"] for r in rows]


def _semantic(
    conn: sqlite3.Connection, query: str, paper_id: int | None, with_refs: bool
) -> list[int]:
    import numpy as np

    name = embedder_name(conn)
    if name is None:
        return []
    store = _vectors(conn)
    if not len(store["ids"]):
        return []
    q = embed_query(name, query)
    if store["m"].shape[1] != q.shape[0]:
        return []                          # stored by a different model
    scores = store["m"] @ q
    mask = np.ones(len(scores), dtype=bool)
    if paper_id is not None:
        mask &= store["paper"] == paper_id
    if not with_refs:
        mask &= ~store["refs"]
    scores = np.where(mask, scores, -np.inf)
    take = min(POOL, int(mask.sum()))
    if take == 0:
        return []
    top = np.argpartition(-scores, take - 1)[:take]
    top = top[np.argsort(-scores[top])]
    return [int(store["ids"][i]) for i in top]


def fuse(rankings: dict[str, list[int]]) -> list[tuple[int, float, list[str]]]:
    """Reciprocal rank fusion: each list adds 1/(k + rank) for every item in it."""
    scores: dict[int, float] = {}
    found_by: dict[int, list[str]] = {}
    for name, ranked in rankings.items():
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
            found_by.setdefault(item, []).append(name)
    order = sorted(scores, key=lambda i: -scores[i])
    return [(i, scores[i], found_by[i]) for i in order]


def search(
    conn: sqlite3.Connection,
    query: str,
    paper_id: int | None = None,
    limit: int = 8,
    mode: str = "hybrid",
    with_references: bool = False,
) -> dict[str, Any]:
    """The passages that best answer a query, each with its paper and page.

    `mode` is hybrid, keyword or semantic. The single-retriever modes exist for
    the evaluation, which measures what each one contributes.
    """
    rankings: dict[str, list[int]] = {}
    note = None
    if mode in ("hybrid", "keyword"):
        rankings["keyword"] = _keyword(conn, query, paper_id, with_references)
    if mode in ("hybrid", "semantic"):
        try:
            rankings["semantic"] = _semantic(conn, query, paper_id, with_references)
        except EmbedderUnavailable as exc:
            note = f"Semantic search is unavailable ({exc}); these are keyword matches only."
    fused = fuse(rankings)[:limit]

    if not fused:
        return {"results": [], "mode": mode, "note": note}
    by_id = {
        r["id"]: r
        for r in conn.execute(
            "SELECT s.id, s.paper_id, s.page, s.text, p.title, p.year FROM passages s "
            "JOIN papers p ON p.id = s.paper_id "
            f"WHERE s.id IN ({','.join('?' * len(fused))})",
            [i for i, _, _ in fused],
        )
    }
    results = [
        {
            "passage_id": i,
            "paper_id": by_id[i]["paper_id"],
            "title": by_id[i]["title"],
            "year": by_id[i]["year"],
            "page": by_id[i]["page"],
            "text": by_id[i]["text"],
            "score": round(score, 5),
            "found_by": found,
        }
        for i, score, found in fused
        if i in by_id
    ]
    out: dict[str, Any] = {"results": results, "mode": mode}
    if note:
        out["note"] = note
    return out


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """How much of the library is searchable, for Settings and the CLI."""
    with_pdf = conn.execute("SELECT COUNT(*) FROM papers WHERE pdf_path IS NOT NULL").fetchone()[0]
    counts = conn.execute(
        "SELECT COUNT(*) AS passages, SUM(embedding IS NOT NULL) AS embedded FROM passages"
    ).fetchone()
    return {
        "papers_with_pdf": with_pdf,
        "papers_stale": len(stale_papers(conn)),
        "passages": counts["passages"] or 0,
        "embedded": counts["embedded"] or 0,
        "embedder": embedder_name(conn),
        "chunker": CHUNKER,
        "model_error": _model_error,
    }


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="./breadcrumbs index",
        description="Cut every stored PDF into passages and embed them, for passage search.",
    )
    parser.add_argument("--rebuild", action="store_true", help="re-index papers already current")
    parser.add_argument("--paper", type=int, action="append", help="only this paper id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="say what would be indexed, change nothing")
    args = parser.parse_args()

    db.init_db()
    conn = db.connect()
    try:
        if args.rebuild:
            targets = args.paper or [
                r["id"] for r in conn.execute("SELECT id FROM papers WHERE pdf_path IS NOT NULL")
            ]
        else:
            targets = stale_papers(conn, args.paper)
        print(f"{len(targets)} paper(s) to index with {embedder_name(conn) or 'keywords only'}")
        if args.dry_run:
            for pid in targets:
                print(f"  would index {pid}")
            return
        for n, pid in enumerate(targets, start=1):
            result = index_paper(conn, pid, force=args.rebuild)
            extra = f" ({result['note']})" if result.get("note") else ""
            print(
                f"[{n}/{len(targets)}] paper {pid}: {result['status']}, "
                f"{result.get('passages', 0)} passages, {result.get('seconds', 0)}s{extra}",
                flush=True,
            )
        s = status(conn)
        print(f"{s['passages']} passages, {s['embedded']} embedded, {s['papers_stale']} stale")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
