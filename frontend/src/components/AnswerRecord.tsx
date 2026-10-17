import { useState } from "react";
import type { Citation, CitationTally, RunRecord } from "../api";

interface Props {
  text: string;
  citations?: Citation[];
  check?: CitationTally;
  run?: RunRecord | null;
}

/** Answers shorter than this get no "own knowledge" label: "Done." needs none. */
const SUBSTANTIAL = 120;

/**
 * Where an uncited answer came from, by the tools its turn called. Names match
 * the backend's tools; one that is not listed counts as none of these.
 */
const LIBRARY_TOOLS = new Set([
  "search_library", "list_papers", "get_paper", "list_authors", "get_author",
  "get_paper_links", "find_author_works", "list_highlights", "pdf_sources",
  "lookup_identifier", "run_sql",
]);
const WEB_TOOLS = new Set(["web_search", "fetch_url", "find_wikipedia"]);
const WRITE_TOOLS = new Set([
  "set_author_homepage", "set_author_bio", "set_author_photo", "attach_pdf",
  "add_note", "add_highlight", "set_paper_status", "create_link",
]);

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The line under an answer saying what kind of statement it is and how much
 * of it was checked, and the record of what produced it.
 *
 * Its job is calibration. An answer can say three kinds of thing, and each is
 * labelled for what it is:
 *
 *   - what a paper says: quoted, and each quote looked for in the paper
 *   - what the library says: read from its records through tools
 *   - what the model knows: its own explanation, nothing looked up
 *
 * A verified quote proves the words are in the paper, not that the sentence
 * around it is right; the line never claims more than was checked.
 */
export default function AnswerRecord({ text, citations, check, run }: Props) {
  const [open, setOpen] = useState(false);
  const tally = check ?? run?.citations ?? tallyOf(citations ?? []);
  const repair = run?.repair;
  const used = new Set(run?.tools_used ?? []);
  const any = (names: Set<string>) => [...used].some((n) => names.has(n));

  let summary: React.ReactNode = null;
  if (tally && tally.total > 0) {
    summary = (
      <>
        {tally.verified > 0 && (
          <span className="ar-ok">✓ {plural(tally.verified, "quote")} found in the paper</span>
        )}
        {tally.failed > 0 && <span className="ar-bad">✗ {tally.failed} not found</span>}
        {tally.unchecked > 0 && <span className="ar-warn">? {tally.unchecked} unchecked</span>}
        <span className="ar-dim">the rest is Bread’s own explanation</span>
      </>
    );
  } else if (run?.absences?.length) {
    // An answer that the paper lacks something, beside the server's own
    // record of searching its text and finding nothing. A literal search, so
    // it proves the words are absent, not that the idea is.
    const terms = [...new Set(run.absences.map((a) => a.query).filter(Boolean))].slice(0, 4);
    summary = (
      <>
        <span className="ar-ok">
          Searched the paper’s text for {terms.map((t) => `“${t}”`).join(", ")}: not found
        </span>
        <span className="ar-dim">the rest is Bread’s own explanation</span>
      </>
    );
  } else if (repair?.reason === "uncited") {
    // Asked once to quote what it attributed to the paper, and still did not.
    summary = (
      <span className="ar-warn">Says what the paper says but quotes none of it: unchecked</span>
    );
  } else if (run && !any(WRITE_TOOLS)) {
    // Reporting what Bread did (attached a file, set a status) needs no label.
    if (any(LIBRARY_TOOLS)) {
      summary = <span className="ar-lib">From your library’s records</span>;
    } else if (any(WEB_TOOLS)) {
      summary = <span className="ar-warn">From web pages Bread read: follow its links to check</span>;
    } else if (text.length > SUBSTANTIAL) {
      summary = run.documents?.length || used.size ? (
        <span className="ar-dim">Bread’s own explanation: nothing here is quoted from the paper</span>
      ) : (
        <span className="ar-dim">Bread’s own knowledge: nothing was looked up</span>
      );
    }
  }

  if (!summary && !run) return null;

  const served = [...new Set((run?.responses ?? []).map((r) => r.model).filter(Boolean))];
  const upstream = [...new Set((run?.responses ?? []).map((r) => r.provider).filter(Boolean))];
  const sent = run?.responses?.[0]?.sent ?? {};
  const tokens = run?.usage?.total_tokens;

  return (
    <div className="ar-root">
      <div className="ar-line">
        {summary}
        {run && (
          <button className="ar-toggle" onClick={() => setOpen((v) => !v)}>
            {open ? "▾" : "▸"} record
          </button>
        )}
      </div>
      {repair && (
        <div className="ar-repair">
          {repair.reason === "uncited"
            ? "The first draft said what the paper says without citing it, so Bread was asked to quote it"
            : repair.wrong_papers?.length && !repair.failed.length
              ? "The first draft suggested a paper under another paper's identifier, so Bread was asked to correct it"
              : `${plural(repair.failed.length + (repair.wrong_papers?.length ?? 0), "citation")} in the first draft could not be verified, so Bread was asked to correct them`}
          {repair.final_check && repair.final_check.failed > 0
            ? `; ${repair.final_check.failed} still could not be found.`
            : "."}
        </div>
      )}
      {open && run && (
        <dl className="ar-detail">
          <dt>model</dt>
          <dd>
            {run.provider} / {run.model}
            {served.length > 0 && served.join(", ") !== run.model && (
              <> (served as {served.join(", ")})</>
            )}
            {upstream.length > 0 && <> via {upstream.join(", ")}</>}
          </dd>
          <dt>settings</dt>
          <dd>
            thinking {run.thinking || "off"}
            {Object.entries(sent)
              .filter(([k]) => k !== "provider")
              .map(([k, v]) => `, ${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
              .join("")}
          </dd>
          <dt>rounds</dt>
          <dd>
            {run.rounds ?? "?"}
            {tokens != null && <>, {tokens.toLocaleString()} tokens</>}
          </dd>
          {!!run.documents?.length && (
            <>
              <dt>read</dt>
              <dd>
                {run.documents.map((d) => (
                  <div key={d.paper_id} title={`SHA-256 ${d.sha256}`}>
                    {d.title ?? `paper ${d.paper_id}`} <code>{d.sha256.slice(0, 12)}</code>
                  </div>
                ))}
              </dd>
            </>
          )}
          <dt>prompt</dt>
          <dd>
            <code title={run.system_sha256}>{run.system_sha256?.slice(0, 12)}</code>, tools{" "}
            <code title={run.tools_sha256}>{run.tools_sha256?.slice(0, 12)}</code>
          </dd>
          <dt>app</dt>
          <dd>
            <code>{run.app_version ?? "unknown"}</code>
          </dd>
          <dt>when</dt>
          <dd>
            {run.started_at} → {run.finished_at ?? "unfinished"} UTC
          </dd>
        </dl>
      )}
    </div>
  );
}

function tallyOf(citations: Citation[]): CitationTally {
  return {
    total: citations.length,
    verified: citations.filter((c) => c.status === "verified").length,
    failed: citations.filter((c) => c.status === "not_found" || c.status === "unknown_paper").length,
    unchecked: citations.filter((c) =>
      ["too_short", "no_quote", "no_source"].includes(c.status),
    ).length,
  };
}
