"""Evaluating the assistant against questions with known answers.

The set lives in eval/questions.json: questions about nine papers, each with a
gold answer, the facts a correct answer must state, and the evidence quoted
from the paper with its page. Some questions have no answer in the paper; the
right response to those is to say so.

    ./breadcrumbs eval check        is the set itself sound? (free, no model)
    ./breadcrumbs eval retrieval    keyword vs semantic vs hybrid passage search (free)
    ./breadcrumbs eval answers --provider deepseek --model deepseek-v4-pro
                                    the whole assistant, end to end (costs tokens)
    ./breadcrumbs eval report       rebuild eval/RESULTS.md from every saved run

`answers` runs against a copy of the library in a temporary folder, so nothing
the assistant writes during the run (a note, a highlight) reaches the real one.
The PDFs are linked rather than copied.

What is scored, and how, is deliberately mechanical. Every metric below is a
rule anyone can re-apply to the saved answers and get the same number:

  correct        every must-mention fact appears in the answer's prose
  grounded       correct, and at least one verified citation to the right paper
  right page     a verified citation lands on a page the evidence is on
  valid quotes   verified citations over all citations
  abstained      for unanswerable questions, the answer says the paper does not
                 contain it (a phrase rule; the report lists them for reading)

`--judge provider:model` adds a model's opinion on correctness and on whether
each cited quote supports its claim. That is the one judgement no rule can
make, and it is reported separately, with its prompt and raw output saved, and
never folded into the mechanical scores.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from . import db, passages, pdftext

ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = ROOT / "eval"
QUESTIONS = EVAL_DIR / "questions.json"
RUNS = EVAL_DIR / "runs"

#: How an answer says the paper does not contain something. A rule, so it can
#: be wrong both ways; every unanswerable item's answer is printed in the
#: report for a reader to check.
ABSTAIN = re.compile(
    r"\b(?:does not|doesn['’]t|do not|don['’]t|did not|didn['’]t|never)\s+(?:\w+\s+){0,4}?"
    r"(?:say|state|mention|report|give|specify|provide|include|discuss|list|use|evaluate|"
    r"test|compare|run|describe|disclose|appear|contain|present|show|apply|name|specify|"
    r"identify|measure|train)"
    r"|\bnot\s+(?:\w+\s+){0,3}?(?:stated|mentioned|reported|specified|given|provided|included|"
    r"discussed|listed|evaluated|tested|disclosed|found|available|covered|addressed|named)"
    r"|\b(?:is|are|was|were)n['’]t\s+(?:\w+\s+){0,2}?(?:stated|specified|mentioned|reported|"
    r"given|named|listed|provided|in\b)"
    r"|\bun(?:stated|specified|reported|mentioned|named)\b"
    r"|\bno\s+(?:\w+\s+){0,3}?(?:mention|report|value|figure|number|results?|baselines?|"
    r"experiments?|evaluation|comparison|information|data)\b"
    r"|\b(?:cannot|can['’]t|could not|couldn['’]t|unable to)\s+(?:\w+\s+){0,2}?(?:find|locate|see)"
    r"|\bnowhere\b|\bis absent\b|\bnot part of\b",
    re.I,
)
CITATION_LINK = re.compile(r"\]\(citation:\d+\)")
#: Markdown emphasis and code marks. Stripped before the abstention rule, which
#: otherwise failed on "does **not report** a CIFAR-10 result".
EMPHASIS = re.compile(r"[*_`]+")


# ---------------------------------------------------------------------------
# the set
# ---------------------------------------------------------------------------
def load_items() -> list[dict[str, Any]]:
    return json.loads(QUESTIONS.read_text())["items"]


def set_hash() -> str:
    return hashlib.sha256(QUESTIONS.read_bytes()).hexdigest()


def resolve(conn: sqlite3.Connection, paper: dict[str, Any]) -> int | None:
    """The library id of an item's paper, found by identifier, not by stored id.

    Ids are local to one library. A DOI or arXiv id is the same in anyone's, so
    the set works in a library other than the one it was written against.
    """
    for column, key in (("doi", "doi"), ("arxiv_id", "arxiv")):
        value = (paper.get(key) or "").strip().lower()
        if value:
            row = conn.execute(
                f"SELECT id FROM papers WHERE lower({column}) = ?", (value,)
            ).fetchone()
            if row:
                return int(row["id"])
    return None


def mentions(text: str, groups: list[list[str]]) -> tuple[int, int]:
    """How many must-mention groups the text satisfies, out of how many."""
    hay = pdftext.fold(text)
    hit = sum(any(pdftext.fold(alt) and pdftext.fold(alt) in hay for alt in g) for g in groups)
    return hit, len(groups)


def cmd_check(_: argparse.Namespace) -> int:
    """Validate the set: papers resolve, PDFs match, every gold quote is where it says."""
    conn = db.connect()
    errors = warnings = 0
    for item in load_items():
        where = f"{item['id']}:"
        pid = resolve(conn, item["paper"])
        if pid is None:
            print(f"ERROR {where} paper not in this library ({item['paper'].get('doi')})")
            errors += 1
            continue
        path = pdftext.pdf_path(conn, pid)
        if path is None:
            print(f"ERROR {where} paper {pid} has no PDF")
            errors += 1
            continue
        if item["paper"].get("pdf_sha256") and pdftext.file_sha256(path) != item["paper"]["pdf_sha256"]:
            print(f"WARN  {where} PDF differs from the one the set was written against; "
                  "page numbers may not match")
            warnings += 1
        if item["kind"] == "unanswerable":
            if item["evidence"] or item["must_mention"]:
                print(f"ERROR {where} unanswerable items carry no evidence or facts")
                errors += 1
            continue
        if not item["evidence"]:
            print(f"ERROR {where} no evidence")
            errors += 1
        for ev in item["evidence"]:
            hit = pdftext.locate_quote(path, ev["quote"], ev["page"])
            if hit is None:
                print(f"ERROR {where} evidence not found: {ev['quote'][:70]!r}")
                errors += 1
            elif hit["page"] != ev["page"]:
                print(f"ERROR {where} evidence on page {hit['page']}, not {ev['page']}")
                errors += 1
        got, of = mentions(item["answer"], item["must_mention"])
        if got != of:
            print(f"ERROR {where} the gold answer itself misses {of - got} must-mention group(s)")
            errors += 1
    items = load_items()
    print(f"{len(items)} items, {errors} error(s), {warnings} warning(s); set sha256 {set_hash()[:16]}")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def cmd_retrieval(args: argparse.Namespace) -> int:
    """Does each search mode surface the gold evidence, and how high?

    Paper-scoped questions search that paper alone, as the assistant does with a
    paper open; a hit is a passage on an evidence page. Library questions search
    everything; a hit is a passage from the right paper (any page), since the
    question is which paper, and on the right page is reported beside it.

    Each question is asked twice: as written, and as paraphrased. The written
    questions were composed while reading the paper and borrow its vocabulary,
    which flatters keyword search. The paraphrases ask the same thing in the
    words someone would use from memory, which is the case semantic search is
    there for. Reporting both shows what each retriever is good at instead of
    one number that depends on how the questions happened to be phrased.
    """
    conn = db.connect()
    ks = sorted(set(args.k))
    modes = ("keyword", "semantic", "hybrid")
    rows: list[dict[str, Any]] = []
    for item in load_items():
        if not item["evidence"]:
            continue
        pid = resolve(conn, item["paper"])
        if pid is None:
            continue
        passages.ensure_indexed(conn, [pid], budget_s=120)
        pages = {ev["page"] for ev in item["evidence"]}
        scope = pid if item["scope"] == "paper" else None
        for variant in ("question", "paraphrase"):
            if not item.get(variant):
                continue
            for mode in modes:
                found = passages.search(
                    conn, item[variant], paper_id=scope, limit=max(ks), mode=mode
                )
                ranked = found["results"]
                first_paper = next(
                    (i for i, r in enumerate(ranked, 1) if r["paper_id"] == pid), None
                )
                first_page = next(
                    (i for i, r in enumerate(ranked, 1)
                     if r["paper_id"] == pid and r["page"] in pages),
                    None,
                )
                rows.append({
                    "id": item["id"], "variant": variant, "scope": item["scope"], "mode": mode,
                    "rank": first_page if item["scope"] == "paper" else first_paper,
                    "page_rank": first_page,
                })

    asked = len({r["id"] for r in rows})
    out = [f"Passage retrieval on {asked} questions with evidence, each asked as written and "
           f"paraphrased (chunker {passages.CHUNKER}, embedder {passages.embedder_name(conn)})", ""]
    header = "| asked | scope | mode | n | " + " | ".join(f"hit@{k}" for k in ks) + " | MRR |"
    out += [header, "|" + "---|" * (5 + len(ks))]
    summary: dict[str, Any] = {}
    for variant in ("question", "paraphrase"):
        for scope in ("paper", "library"):
            for mode in modes:
                sel = [r for r in rows
                       if r["variant"] == variant and r["scope"] == scope and r["mode"] == mode]
                if not sel:
                    continue
                hits = [sum(1 for r in sel if r["rank"] and r["rank"] <= k) / len(sel) for k in ks]
                mrr = sum(1 / r["rank"] for r in sel if r["rank"]) / len(sel)
                summary[f"{variant}/{scope}/{mode}"] = {
                    "n": len(sel), "hits": dict(zip(ks, hits)), "mrr": mrr,
                }
                label = "as written" if variant == "question" else "paraphrased"
                out.append(
                    f"| {label} | {scope} | {mode} | {len(sel)} | "
                    + " | ".join(f"{h:.0%}" for h in hits) + f" | {mrr:.2f} |"
                )
    out.append("")
    out.append("Paper scope: a passage from an evidence page, searching that paper. Library "
               "scope: a passage from the right paper, searching everything.")
    text = "\n".join(out)
    print(text)
    if args.save:
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / "retrieval.md").write_text(text + "\n")
        (RUNS / "retrieval.json").write_text(json.dumps(
            {"set_sha256": set_hash(), "chunker": passages.CHUNKER,
             "embedder": passages.embedder_name(conn), "summary": summary, "rows": rows},
            indent=1,
        ) + "\n")
        print(f"saved {RUNS / 'retrieval.md'}")
    return 0


# ---------------------------------------------------------------------------
# answers, end to end
# ---------------------------------------------------------------------------
def _sandbox() -> Path:
    """A throwaway library: a consistent copy of the database, the files linked."""
    home = Path(tempfile.mkdtemp(prefix="breadcrumbs-eval-"))
    source = db.connect()
    target = sqlite3.connect(home / "library.db")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    real = db.library_root()
    for name in ("pdfs", "authors"):
        if (real / name).exists():
            (home / name).symlink_to(real / name, target_is_directory=True)
    return home


def _context(conn: sqlite3.Connection, item: dict[str, Any], pid: int) -> dict[str, Any]:
    """What the app would send: the reader on page 1, or the grid with nothing selected."""
    if item["scope"] == "library":
        papers = conn.execute(
            "SELECT id, title FROM papers ORDER BY year DESC NULLS LAST, title LIMIT 40"
        ).fetchall()
        return {"tab": "timeline", "visible_papers": [dict(p) for p in papers]}
    row = conn.execute("SELECT id, title, year FROM papers WHERE id = ?", (pid,)).fetchone()
    path = pdftext.pdf_path(conn, pid)
    pages = pdftext.extract_text(path)
    return {
        "tab": "reader",
        "paper": dict(row),
        "reading": {
            "page": 1, "page_count": len(pages), "text": pages[0]["text"],
            "outline": pdftext.outline(path),
        },
    }


async def _ask(item: dict[str, Any], pid: int) -> dict[str, Any]:
    from .ai import agent

    with db.session() as conn:
        session_id = agent.create_session(conn, f"eval {item['id']}")
        context = _context(conn, item, pid)
    started = time.monotonic()
    record: dict[str, Any] = {"tools": [], "repair": None, "error": None}
    # The answer is everything the turn said, not only its last message: a
    # model often states the finding beside a tool call and closes with one
    # line. Citations are renumbered so they stay distinct across messages.
    parts: list[str] = []
    cited: list[dict[str, Any]] = []

    def take(text: str, found: list[dict[str, Any]]) -> None:
        offset = len(cited)
        parts.append(re.sub(
            r"\(citation:(\d+)\)", lambda m: f"(citation:{int(m[1]) + offset})", text or ""
        ))
        cited.extend({**c, "n": c["n"] + offset} for c in found or [])

    async for event, data in agent.run_turn(session_id, item["question"], context):
        if event == "tool_call":
            record["tools"].append(data["name"])
        elif event == "check":
            record["repair"] = data
            # The corrected answer replaces everything said so far.
            parts.clear()
            cited.clear()
        elif event == "text":
            take(data["text"], data.get("citations") or [])
        elif event == "message":
            take(data["text"], data["citations"])
            record.update(check=data["check"], run=data["run"])
        elif event == "error":
            record["error"] = data["message"]
    record["text"] = "\n\n".join(p for p in parts if p.strip())
    record["citations"] = cited
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def score(item: dict[str, Any], pid: int, rec: dict[str, Any]) -> dict[str, Any]:
    """The mechanical scores for one answer. See the module docstring."""
    text = rec.get("text") or ""
    prose = CITATION_LINK.sub("]", text)
    cites = rec.get("citations") or []
    verified = [c for c in cites if c["status"] == "verified"]
    pages = {ev["page"] for ev in item["evidence"]}
    out: dict[str, Any] = {
        "answered": bool(text) and not rec.get("error"),
        "citations": len(cites),
        "verified": len(verified),
        "failed": sum(c["status"] in ("not_found", "unknown_paper") for c in cites),
        "repaired": rec.get("repair") is not None,
    }
    if item["kind"] == "unanswerable":
        out["abstained"] = bool(ABSTAIN.search(EMPHASIS.sub("", prose)))
        return out
    got, of = mentions(prose, item["must_mention"])
    out["facts"] = f"{got}/{of}"
    out["correct"] = out["answered"] and got == of
    out["cited_paper"] = any(c["paper_id"] == pid for c in verified)
    out["right_page"] = any(
        c["paper_id"] == pid and c.get("where") == "pdf" and c.get("page") in pages
        for c in verified
    )
    out["grounded"] = out["correct"] and out["cited_paper"]
    return out


JUDGE_SYSTEM = """\
You grade an AI research assistant's answer to a question about a scientific paper.
You are given the question, the gold answer written from the paper, grading notes,
the assistant's answer, and each quote it cited (already confirmed to appear in the
paper word for word). Judge only against the gold answer and the quotes; do not use
outside knowledge of the paper.

Definitions:
- correct: the answer states the gold answer's facts. For a question the paper cannot
  answer, correct means it says so and does not invent an answer.
- abstained: true if the answer says the paper does not contain what was asked, even
  if it goes on to give related information that the paper does contain. False if it
  gives an answer to the question as asked, or gives no answer at all.
- unsupported_claims: statements presented as what the paper says that neither the
  gold answer nor the cited quotes support. Do not count the assistant describing its
  own searches, or background it presents as its own explanation.

Return JSON:
{"correct": "yes" | "partial" | "no",
 "abstained": true | false,
 "citations": [{"n": 1, "supports": "yes" | "partial" | "no"}],  // does each quote support the claim it is attached to
 "unsupported_claims": ["..."],
 "rationale": "one or two sentences"}"""


async def _judge(client: Any, judge: tuple[str, str, str], item: dict[str, Any],
                 rec: dict[str, Any]) -> dict[str, Any]:
    from .ai import providers

    provider, model, key = judge
    cites = "\n".join(
        f"[{c['n']}] ({c['status']}) \"{c['quote']}\"" for c in rec.get("citations") or []
    ) or "(none)"
    user = (
        f"Question: {item['question']}\n\n"
        f"Gold answer: {item['answer'] if item['kind'] != 'unanswerable' else 'The paper does not contain this. ' + item['answer']}\n\n"
        f"Grading notes: {item.get('notes') or '-'}\n\n"
        f"Assistant's answer:\n{rec.get('text') or '(no answer)'}\n\n"
        f"Cited quotes:\n{cites}"
    )
    try:
        verdict, usage = await providers.complete_json(
            client, provider, model, key, JUDGE_SYSTEM, user, temperature=0.0
        )
    except Exception as exc:          # a judge failure is reported, not fatal
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"verdict": verdict, "usage": usage, "prompt": user}


async def _run_answers(args: argparse.Namespace) -> int:
    items = [i for i in load_items() if not args.only or i["id"] in args.only]
    real_home = db.library_root()
    with db.session() as conn:
        resolved = {i["id"]: resolve(conn, i["paper"]) for i in items}
        judge_key = ""
        if args.judge:
            from .ai import providers

            jp = args.judge.split(":", 1)[0]
            judge_key = db.get_setting(conn, providers.PROVIDERS[jp].setting)
    missing = [k for k, v in resolved.items() if v is None]
    if missing:
        print(f"skipping {len(missing)} item(s) whose paper is not in this library: {missing}")
    items = [i for i in items if resolved[i["id"]] is not None]

    home = _sandbox()
    os.environ["BREADCRUMBS_HOME"] = str(home)
    try:
        db.init_db()
        with db.session() as conn:
            db.set_settings(conn, {
                "assistant_provider": args.provider,
                "assistant_model": args.model,
                "assistant_thinking": args.thinking,
                "assistant_route": args.route or "",
            })
            embedder = passages.embedder_name(conn)
        print(f"{len(items)} questions on {args.provider}/{args.model} "
              f"(thinking {args.thinking}), sandbox {home}")

        gate = asyncio.Semaphore(args.concurrency)
        results: dict[str, dict[str, Any]] = {}

        async def one(item: dict[str, Any]) -> None:
            async with gate:
                pid = resolved[item["id"]]
                try:
                    rec = await _ask(item, pid)
                except Exception as exc:
                    rec = {"error": f"{type(exc).__name__}: {exc}", "tools": []}
                rec["score"] = score(item, pid, rec)
                results[item["id"]] = rec
                s = rec["score"]
                mark = (
                    ("abstained" if s.get("abstained") else "ANSWERED") if item["kind"] == "unanswerable"
                    else ("ok" if s.get("correct") else "WRONG") + (" grounded" if s.get("grounded") else "")
                )
                print(f"  {item['id']:<12} {mark:<20} cites {s['verified']}/{s['citations']} "
                      f"{rec.get('seconds', 0)}s {rec.get('error') or ''}", flush=True)

        await asyncio.gather(*(one(i) for i in items))

        if args.judge:
            from .sources.http import make_client

            provider, model = args.judge.split(":", 1)
            async with make_client() as client:
                for item in items:
                    results[item["id"]]["judge"] = await _judge(
                        client, (provider, model, judge_key), item, results[item["id"]]
                    )
    finally:
        os.environ["BREADCRUMBS_HOME"] = str(real_home)
        if not args.keep_sandbox:
            shutil.rmtree(home, ignore_errors=True)

    from .ai import provenance

    from .ai import agent

    run = {
        "format": "breadcrumbs-eval-run/1",
        "provider": args.provider, "model": args.model, "thinking": args.thinking,
        "route": args.route or None, "judge": args.judge,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_version": provenance.app_version(), "set_sha256": set_hash(),
        # The commit alone cannot tell apart runs made on uncommitted edits;
        # the prompt and the tool definitions are what an answer depends on.
        "prompt_sha256": provenance.sha256_text(agent.SYSTEM_PROMPT),
        "tools_sha256": provenance.tools_sha256(),
        "chunker": passages.CHUNKER, "embedder": embedder,
        "items": [
            {"id": i["id"], "kind": i["kind"], "scope": i["scope"], "question": i["question"],
             **results[i["id"]]}
            for i in items
        ],
    }
    run["summary"] = summarise(run)
    RUNS.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9.]+", "-", f"{args.provider}-{args.model}-{args.thinking}".lower())
    stem = f"{time.strftime('%Y%m%d-%H%M')}-{slug}"
    (RUNS / f"{stem}.json").write_text(json.dumps(run, indent=1, ensure_ascii=False, default=str) + "\n")
    (RUNS / f"{stem}.md").write_text(run_report(run))
    print(f"saved {RUNS / stem}.json and .md")
    print(json.dumps(run["summary"], indent=1))
    write_results()
    return 0


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def summarise(run: dict[str, Any]) -> dict[str, Any]:
    items = run["items"]
    ans = [i for i in items if i["kind"] != "unanswerable"]
    paper = [i for i in ans if i["scope"] == "paper"]
    un = [i for i in items if i["kind"] == "unanswerable"]
    cites = sum(i["score"]["citations"] for i in items)
    tokens = [
        (i.get("run") or {}).get("usage", {}).get("total_tokens", 0) for i in items if i.get("run")
    ]
    out: dict[str, Any] = {
        "n": len(items),
        "errors": sum(bool(i.get("error")) for i in items),
        "correct": _rate([i["score"].get("correct", False) for i in ans]),
        "grounded": _rate([i["score"].get("grounded", False) for i in ans]),
        "right_page": _rate([i["score"].get("right_page", False) for i in paper]),
        "valid_quotes": (sum(i["score"]["verified"] for i in items) / cites) if cites else None,
        "citations_per_answer": cites / len(items) if items else 0,
        "abstained": _rate([i["score"].get("abstained", False) for i in un]),
        "repairs": sum(i["score"]["repaired"] for i in items),
        "tokens_per_question": (sum(tokens) / len(tokens)) if tokens else None,
        "seconds_per_question": sum(i.get("seconds", 0) for i in items) / len(items) if items else 0,
    }
    judged = [i for i in items if (i.get("judge") or {}).get("verdict")]
    if judged:
        verdicts = [i["judge"]["verdict"] for i in judged]
        support = [
            c.get("supports") == "yes"
            for v in verdicts for c in (v.get("citations") or []) if isinstance(c, dict)
        ]
        out["judge"] = {
            "n": len(judged),
            "correct": _rate([v.get("correct") == "yes" for i, v in zip(judged, verdicts)
                              if i["kind"] != "unanswerable"]),
            "abstained": _rate([bool(v.get("abstained")) for i, v in zip(judged, verdicts)
                                if i["kind"] == "unanswerable"]),
            "quote_supports_claim": _rate(support),
            "answers_with_unsupported_claims": _rate(
                [bool(v.get("unsupported_claims")) for v in verdicts]
            ),
        }
    return out


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.0%}"


def run_report(run: dict[str, Any]) -> str:
    s = run["summary"]
    lines = [
        f"# {run['provider']} / {run['model']} (thinking {run['thinking']})",
        "",
        f"Run {run['started']}, app {run['app_version']}, set {run['set_sha256'][:12]}, "
        f"embedder {run['embedder']}.",
        "",
        f"- correct: {_pct(s['correct'])}   grounded: {_pct(s['grounded'])}   "
        f"right page: {_pct(s['right_page'])}",
        f"- valid quotes: {_pct(s['valid_quotes'])} of {s['citations_per_answer']:.1f} per answer; "
        f"repairs {s['repairs']}",
        f"- abstained on unanswerable: {_pct(s['abstained'])}",
        f"- errors: {s['errors']}",
    ]
    if s.get("judge"):
        j = s["judge"]
        lines.append(
            f"- judge ({run['judge']}): correct {_pct(j['correct'])}, abstained "
            f"{_pct(j['abstained'])}, quote supports claim {_pct(j['quote_supports_claim'])}, "
            f"answers with uncited extra detail {_pct(j['answers_with_unsupported_claims'])}"
        )
    lines += ["", "| id | kind | result | facts | quotes | right page | tools |", "|---|---|---|---|---|---|---|"]
    for i in run["items"]:
        sc = i["score"]
        result = (
            ("abstained" if sc.get("abstained") else "**answered**") if i["kind"] == "unanswerable"
            else ("correct" if sc.get("correct") else "**wrong**")
        )
        if i.get("error"):
            result = f"**error**: {i['error'][:60]}"
        lines.append(
            f"| {i['id']} | {i['kind']} | {result} | {sc.get('facts', '')} | "
            f"{sc['verified']}/{sc['citations']}{' (repaired)' if sc['repaired'] else ''} | "
            f"{'yes' if sc.get('right_page') else ''} | {len(i.get('tools') or [])} |"
        )
    lines += ["", "## Answers to the unanswerable questions", ""]
    for i in run["items"]:
        if i["kind"] == "unanswerable":
            lines += [f"**{i['id']}**: {i['question']}", "", "> " + (i.get("text") or "(none)").replace("\n", "\n> "), ""]
    lines += ["## Wrong answers", ""]
    for i in run["items"]:
        if i["kind"] != "unanswerable" and not i["score"].get("correct"):
            lines += [f"**{i['id']}** ({i['score'].get('facts')}): {i['question']}", "",
                      "> " + (i.get("text") or i.get("error") or "(none)").replace("\n", "\n> "), ""]
    return "\n".join(lines) + "\n"


def write_results() -> None:
    """eval/RESULTS.md: every saved run in one table, newest first."""
    runs = []
    for path in sorted(RUNS.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if data.get("format") == "breadcrumbs-eval-run/1":
            runs.append((path, data))
    lines = [
        "# Evaluation results",
        "",
        "Generated by `./breadcrumbs eval report` from the runs in `eval/runs/`. "
        "How each number is computed is in `eval/README.md`.",
        "",
        "| model | thinking | n | correct | grounded | right page | valid quotes | quotes/answer | "
        "abstained | repairs | errors | tokens/q | prompt |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for path, run in runs:
        s = run["summary"]
        lines.append(
            f"| [{run['provider']}/{run['model']}](runs/{path.stem}.md) | {run['thinking']} | {s['n']} | "
            f"{_pct(s['correct'])} | {_pct(s['grounded'])} | {_pct(s['right_page'])} | "
            f"{_pct(s['valid_quotes'])} | {s['citations_per_answer']:.1f} | {_pct(s['abstained'])} | "
            f"{s['repairs']} | {s['errors']} | {round(s['tokens_per_question'] or 0):,} | "
            f"{(run.get('prompt_sha256') or '–')[:8]} |"
        )
    lines += [
        "",
        "`prompt` is a hash of the system prompt the run used. `–` means the run predates "
        "recording it; those runs used the prompt from before findings 6 and 7 in the README "
        "(quotes shown only as chips, no search-miss nudge). One run per row: a difference of "
        "one or two questions (3–5 points) is within run-to-run noise.",
    ]
    judged = [(p, r) for p, r in runs if r["summary"].get("judge")]
    if judged:
        lines += ["", "With a model as judge (an opinion, not a measurement):", "",
                  "| model | judge | correct | abstained | quote supports claim | answers with uncited extra detail |",
                  "|---|---|---|---|---|---|"]
        for path, run in judged:
            j = run["summary"]["judge"]
            lines.append(
                f"| {run['provider']}/{run['model']} | {run['judge']} | {_pct(j['correct'])} | "
                f"{_pct(j['abstained'])} | {_pct(j['quote_supports_claim'])} | "
                f"{_pct(j['answers_with_unsupported_claims'])} |"
            )
    retrieval = RUNS / "retrieval.md"
    if retrieval.exists():
        lines += ["", "## Passage retrieval", "", retrieval.read_text().strip()]
    (EVAL_DIR / "RESULTS.md").write_text("\n".join(lines) + "\n")


def rescore() -> int:
    """Re-apply the current scoring rules to every saved run's answers.

    The mechanical scores are rules over saved text, so a corrected rule is
    applied to past runs without asking any model again. The answers, the
    citations and the judge's verdicts are left exactly as they were.
    """
    items = {i["id"]: i for i in load_items()}
    conn = db.connect()
    changed = 0
    for path in sorted(RUNS.glob("*.json")):
        run = json.loads(path.read_text())
        if run.get("format") != "breadcrumbs-eval-run/1":
            continue
        for rec in run["items"]:
            item = items.get(rec["id"])
            pid = resolve(conn, item["paper"]) if item else None
            if item is not None and pid is not None:
                rec["score"] = score(item, pid, rec)
        run["summary"] = summarise(run)
        path.write_text(json.dumps(run, indent=1, ensure_ascii=False, default=str) + "\n")
        path.with_suffix(".md").write_text(run_report(run))
        changed += 1
    return changed


async def _rejudge(judge: str, only: list[str] | None) -> int:
    from .ai import providers
    from .sources.http import make_client

    provider, model = judge.split(":", 1)
    with db.session() as conn:
        key = db.get_setting(conn, providers.PROVIDERS[provider].setting)
    items = {i["id"]: i for i in load_items()}
    done = 0
    async with make_client() as client:
        for path in sorted(RUNS.glob("*.json")):
            run = json.loads(path.read_text())
            if run.get("format") != "breadcrumbs-eval-run/1":
                continue
            if only and not any(o in path.name for o in only):
                continue
            for rec in run["items"]:
                if rec["id"] in items:
                    rec["judge"] = await _judge(client, (provider, model, key), items[rec["id"]], rec)
            run["judge"] = judge
            run["summary"] = summarise(run)
            path.write_text(json.dumps(run, indent=1, ensure_ascii=False, default=str) + "\n")
            path.with_suffix(".md").write_text(run_report(run))
            print(f"re-judged {path.name}", flush=True)
            done += 1
    return done


def cmd_judge(args: argparse.Namespace) -> int:
    """Grade saved answers again with a judge model. The answers are not re-run."""
    print(f"re-judged {asyncio.run(_rejudge(args.judge, args.runs))} run(s)")
    write_results()
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    print(f"re-scored {rescore()} run(s) with the current rules")
    write_results()
    print(f"wrote {EVAL_DIR / 'RESULTS.md'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="./breadcrumbs eval", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate the question set against this library").set_defaults(fn=cmd_check)

    r = sub.add_parser("retrieval", help="compare keyword, semantic and hybrid passage search")
    r.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    r.add_argument("--save", action="store_true", help="write eval/runs/retrieval.{md,json}")
    r.set_defaults(fn=cmd_retrieval)

    a = sub.add_parser("answers", help="run the assistant on every question (costs tokens)")
    a.add_argument("--provider", required=True)
    a.add_argument("--model", required=True)
    a.add_argument("--thinking", default="off", help="off, low, medium or high")
    a.add_argument("--route", help="OpenRouter upstream provider to pin")
    a.add_argument("--only", nargs="+", help="item ids to run")
    a.add_argument("--concurrency", type=int, default=3)
    a.add_argument("--judge", help="provider:model to grade answers and citation support")
    a.add_argument("--keep-sandbox", action="store_true", help="keep the throwaway library")
    a.set_defaults(fn=lambda ns: asyncio.run(_run_answers(ns)))

    sub.add_parser(
        "report", help="re-score saved runs with the current rules, rebuild eval/RESULTS.md"
    ).set_defaults(fn=cmd_report)

    j = sub.add_parser("judge", help="grade saved answers again with a judge model")
    j.add_argument("--judge", required=True, help="provider:model")
    j.add_argument("--runs", nargs="+", help="only runs whose file name contains one of these")
    j.set_defaults(fn=cmd_judge)

    args = parser.parse_args()
    db.init_db()
    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    main()
