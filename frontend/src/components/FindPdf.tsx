import { useCallback, useEffect, useRef, useState } from "react";
import { api, pdfSearchStream } from "../api";
import type { PdfCandidate, PdfStage } from "../api";
import { notifyChange } from "../live";
import "./FindPdf.css";

interface Props {
  paperId: number;
  /** Whether a file is already stored, which decides the wording. */
  hasPdf: boolean;
}

/** One line in the log, in the order it happened. */
type Entry =
  | { kind: "stage"; stage: PdfStage }
  | { kind: "candidate"; candidate: PdfCandidate }
  | { kind: "attempt"; index: number; label: string; url: string; note: string | null }
  | {
      kind: "result";
      index: number;
      ok: boolean;
      detail: string;
      followed?: number;
      blocked?: boolean;
      url?: string;
    }
  | { kind: "note"; text: string };

/**
 * Search for a paper's PDF, with the hunt shown as it happens.
 *
 * Adding a paper deliberately fetches no file, so this is where one is found.
 * The search is worth watching rather than hiding behind a spinner: most of
 * this library is paywalled, so it fails often, and "no PDF" is a much less
 * useful answer than seeing that four repositories were asked, three answered
 * with a login page, and the fourth had it.
 */
export default function FindPdf({ paperId, hasPdf }: Props) {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [outcome, setOutcome] = useState<{
    ok: boolean;
    message: string;
    blocked?: PdfCandidate[];
    attempted?: PdfCandidate[];
  } | null>(null);
  const close = useRef<(() => void) | null>(null);
  const log = useRef<HTMLDivElement>(null);

  const push = useCallback((entry: Entry) => setEntries((prev) => [...prev, entry]), []);

  // The interesting line is always the newest one.
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [entries.length]);

  const start = useCallback(() => {
    setOpen(true);
    setRunning(true);
    setEntries([]);
    setOutcome(null);
    close.current?.();
    close.current = pdfSearchStream(paperId, {
      onStart: (d) =>
        push({
          kind: "note",
          text: d.has_pdf
            ? "This paper already has a PDF. Searching for a replacement."
            : "Looking for a copy of this paper.",
        }),
      // A stage already listed as running is updated in place rather than
      // repeated, so the log reads as four sources resolving, not eight lines.
      onStage: (stage) =>
        setEntries((prev) => {
          const at = prev.findIndex(
            (e) => e.kind === "stage" && e.stage.name === stage.name,
          );
          if (at < 0) return [...prev, { kind: "stage", stage }];
          const next = [...prev];
          next[at] = { kind: "stage", stage };
          return next;
        }),
      onCandidate: (candidate) => push({ kind: "candidate", candidate }),
      onTrying: (d) =>
        push({
          kind: "note",
          text: `Trying ${d.count} link${d.count === 1 ? "" : "s"}, most promising first.`,
        }),
      onAttempt: (d) =>
        push({ kind: "attempt", index: d.index, label: d.label, url: d.url, note: d.note }),
      onAttemptResult: (d) =>
        push({
          kind: "result",
          index: d.index,
          ok: d.ok,
          detail: d.detail,
          followed: d.followed,
          blocked: d.blocked,
          url: d.url,
        }),
      onDone: (d) => {
        setRunning(false);
        setOutcome({
          ok: d.ok,
          message: d.message,
          blocked: d.blocked,
          attempted: d.attempted,
        });
        if (d.ok) {
          // The badge, the reader and the timeline all read this.
          notifyChange({ kind: "paper", id: paperId, source: "pdf-search" });
        }
      },
      onError: (message) => {
        setRunning(false);
        setOutcome({ ok: false, message });
      },
    });
  }, [paperId, push]);

  const dismiss = useCallback(() => {
    close.current?.();
    close.current = null;
    setRunning(false);
    setOpen(false);
  }, []);

  // Closing the tab or navigating away must not leave the stream open.
  useEffect(() => () => close.current?.(), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismiss]);

  return (
    <>
      <button
        className="app-find-pdf"
        onClick={start}
        title={
          hasPdf
            ? "Search for a copy again, replacing the stored one"
            : "Search the open-access sources for this paper's PDF"
        }
      >
        {hasPdf ? "Find PDF again" : "Find PDF"}
      </button>

      {open && (
        <div className="fp-backdrop" onMouseDown={dismiss}>
          <div
            className="fp-card"
            role="dialog"
            aria-modal="true"
            aria-label="Searching for a PDF"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="fp-bar">
              <span className="fp-kicker">Finding a PDF</span>
              {running && <span className="fp-pulse" aria-hidden="true" />}
              <span className="fp-state">{running ? "searching…" : "finished"}</span>
              <button className="fp-close" onClick={dismiss} aria-label="Close">
                ✕
              </button>
            </div>

            <div className="fp-log" ref={log}>
              {entries.map((entry, i) => (
                <Line key={i} entry={entry} />
              ))}
            </div>

            {outcome && (
              <div className={`fp-outcome ${outcome.ok ? "is-ok" : "is-bad"}`}>
                <p>{outcome.message}</p>
                {!outcome.ok && !!(outcome.blocked?.length || outcome.attempted?.length) && (
                  <>
                    <p className="fp-tried-head">
                      {outcome.blocked?.length
                        ? "Blocked, but these open in a browser:"
                        : "Links tried — one of these may still open for you:"}
                    </p>
                    <ul className="fp-blocked">
                      {(outcome.blocked?.length ? outcome.blocked : outcome.attempted ?? []).map(
                        (c) => (
                          <li key={c.url}>
                            <a href={c.url} target="_blank" rel="noreferrer">
                              {c.label}
                            </a>
                            <Url url={c.url} />
                          </li>
                        ),
                      )}
                    </ul>
                  </>
                )}
                <div className="fp-actions">
                  {outcome.ok && (
                    <a
                      className="fp-open"
                      href={api.pdfUrl(paperId)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open the PDF
                    </a>
                  )}
                  {!outcome.ok && (
                    <button className="fp-retry" onClick={start}>
                      Search again
                    </button>
                  )}
                  <button className="fp-done" onClick={dismiss}>
                    Close
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/** A URL, always openable. Truncated to keep one line one line. */
function Url({ url }: { url: string }) {
  return (
    <a className="fp-url" href={url} target="_blank" rel="noreferrer" title={url}>
      {url}
    </a>
  );
}

/** One log line. Kept apart so the reducer above stays readable. */
function Line({ entry }: { entry: Entry }) {
  if (entry.kind === "note") {
    return <p className="fp-note">{entry.text}</p>;
  }
  if (entry.kind === "stage") {
    const { label, status, detail } = entry.stage;
    return (
      <p className={`fp-stage is-${status}`}>
        <span className="fp-dot" aria-hidden="true" />
        <span className="fp-label">{label}</span>
        <span className="fp-detail">{detail}</span>
      </p>
    );
  }
  if (entry.kind === "candidate") {
    const { label, note, url } = entry.candidate;
    return (
      <p className="fp-candidate">
        <span className="fp-plus" aria-hidden="true">
          +
        </span>
        <span className="fp-label">{label}</span>
        {note && <span className="fp-detail">{note}</span>}
        <Url url={url} />
      </p>
    );
  }
  if (entry.kind === "attempt") {
    return (
      <p className="fp-attempt">
        <span className="fp-arrow" aria-hidden="true">
          →
        </span>
        <span className="fp-label">{entry.label}</span>
        <Url url={entry.url} />
      </p>
    );
  }
  const tone = entry.ok ? "is-ok" : entry.blocked ? "is-blocked" : "is-bad";
  return (
    <p className={`fp-result ${tone}`}>
      <span className="fp-mark" aria-hidden="true">
        {entry.ok ? "✓" : entry.blocked ? "⊘" : "✕"}
      </span>
      <span className="fp-detail">{entry.detail}</span>
      {!!entry.followed && (
        <span className="fp-followed">
          found {entry.followed} link{entry.followed === 1 ? "" : "s"} inside
        </span>
      )}
      {!entry.ok && entry.url && (
        <a className="fp-manual" href={entry.url} target="_blank" rel="noreferrer">
          {entry.blocked ? "open in browser →" : "try in browser →"}
        </a>
      )}
    </p>
  );
}
