import { useState } from "react";
import type { Citation, CitationTally, RunRecord } from "../api";

interface Props {
  text: string;
  citations?: Citation[];
  check?: CitationTally;
  run?: RunRecord | null;
}

/** Answers shorter than this with no citations are not flagged: "Done." needs none. */
const SUBSTANTIAL = 280;

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The line under an answer saying how much of it was checked, and the record
 * of what produced it.
 *
 * Its job is calibration. A verified quote proves the words are in the paper,
 * not that the sentence around it is right, and uncited prose is the model's
 * own; the line says which is which rather than letting a confident answer
 * imply it was all checked.
 */
export default function AnswerRecord({ text, citations, check, run }: Props) {
  const [open, setOpen] = useState(false);
  const tally = check ?? run?.citations ?? tallyOf(citations ?? []);
  const repair = run?.repair;

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
  } else if (run && text.length > SUBSTANTIAL) {
    // Only where it tells the reader something. An answer reporting what
    // Bread did (attached a file, set a status) needs no citation, and older
    // answers have no record to say what was read.
    if (run.documents?.length) {
      summary = (
        <span className="ar-warn">
          No citations: nothing here was checked against the paper Bread read
        </span>
      );
    } else if ((run.rounds ?? 0) <= 1) {
      summary = <span className="ar-dim">Bread’s own knowledge: nothing was looked up</span>;
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
            : `${plural(repair.failed.length, "quote")} in the first draft could not be verified, so Bread was asked to correct ${repair.failed.length === 1 ? "it" : "them"}`}
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
