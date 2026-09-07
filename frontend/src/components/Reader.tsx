import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ChatContext, Highlight, Paper, PdfOptions } from "../api";
import Assistant from "./Assistant";
import AutoTextarea from "./AutoTextarea";
import PdfView from "./PdfView";
import Resizer, { useStoredWidth } from "./Resizer";
import type { Selection as PdfSelection } from "./PdfView";
import { notifyChange, onChange } from "../live";
import { STATUSES, statusLabel } from "../status";
import "./Reader.css";
import Abstract from "./Abstract";

interface Props {
  paperId: number;
}

const COLORS = ["#fde047", "#86efac", "#93c5fd", "#f9a8d4", "#fdba74"];

/**
 * A standalone page for reading one paper, opened in its own tab.
 *
 * Reading is where the assistant earns its keep, so it is here too: a question
 * about the passage in front of you should not mean going back to another
 * window to ask it.
 */
const INVERT_KEY = "breadcrumbs.reader.invert";

export default function Reader({ paperId }: Props) {
  const [paper, setPaper] = useState<Paper | null>(null);
  const [options, setOptions] = useState<PdfOptions | null>(null);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [version, setVersion] = useState(0);

  const [selection, setSelection] = useState<PdfSelection | null>(null);
  const [noteDraft, setNoteDraft] = useState<string | null>(null);

  /**
   * Change what is selected, and start the menu fresh.
   *
   * The draft belongs to one selection. Left standing, it survived the passage
   * it was written for: dismissing the menu cleared the selection but not the
   * draft, so the next passage you selected opened straight into a note field
   * for text it had nothing to do with.
   */
  const chooseSelection = useCallback((next: PdfSelection | null) => {
    setSelection(next);
    setNoteDraft(null);
  }, []);
  const [activeHighlight, setActiveHighlight] = useState<number | null>(null);
  /**
   * Inverting the page is a reading preference, not a per-paper one — it
   * depends on the room you are in. Every reader tab shares one setting,
   * because localStorage is per origin and each paper opens in its own tab.
   */
  const [dark, setDark] = useState(() => {
    try {
      return localStorage.getItem(INVERT_KEY) === "1";
    } catch {
      return false;   // a blocked store just means the default
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(INVERT_KEY, dark ? "1" : "0");
    } catch {
      /* not remembering it is not worth surfacing */
    }
  }, [dark]);
  const [sideTab, setSideTab] = useState<"notes" | "chat">("notes");
  const [reveal, setReveal] = useState<{
    page: number;
    rects: { x: number; y: number; w: number; h: number }[];
    token: number;
    flash?: boolean;
  } | null>(null);

  // Remembered: reading is a long sitting, and widening the panel again on
  // every visit would be tiresome.
  const [sideWidth, setSideWidth] = useStoredWidth("breadcrumbs.reader.side", 460);
  const [attachment, setAttachment] = useState<{ page: number; text: string } | null>(null);
  const [visiblePage, setVisiblePage] = useState(1);
  const [pageText, setPageText] = useState<{ page: number; text: string; count: number } | null>(
    null,
  );
  const [outline, setOutline] = useState<
    { level: number; title: string; page: number }[]
  >([]);

  // Changes made in the main window reach this tab over the broadcast channel.
  useEffect(() => onChange(() => setVersion((v) => v + 1)), []);

  useEffect(() => {
    api
      .paper(paperId)
      .then((p) => {
        setPaper(p);
        setNote(p.note ?? "");
        document.title = `${p.title} · Breadcrumbs`;
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
    api.pdfOptions(paperId).then(setOptions).catch(() => setOptions(null));
    api
      .highlights(paperId)
      .then((d) => {
        setHighlights(d.highlights);
        // Opened from another tab with a highlight in the URL: go to it once
        // the marks are known.
        const wanted = Number(new URLSearchParams(window.location.search).get("highlight"));
        const target = d.highlights.find((h) => h.id === wanted);
        if (target) {
          setActiveHighlight(target.id);
          setReveal({
            page: target.page, rects: target.rects, token: Date.now(), flash: false,
          });
        }
      })
      .catch(() => undefined);
    api.pdfOutline(paperId).then((d) => setOutline(d.outline)).catch(() => setOutline([]));
  }, [paperId, version]);

  useEffect(() => {
    const DOUBLE_TAP_MS = 400;
    let lastShift = 0;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Shift") {
        lastShift = 0;
        return;
      }
      if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      const now = Date.now();
      if (now - lastShift < DOUBLE_TAP_MS) {
        lastShift = 0;
        setSideTab((t) => (t === "chat" ? "notes" : "chat"));
      } else {
        lastShift = now;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // The text of the page in view, so a question about it needs no tool call.
  // Fetched on a delay: scrolling through a paper should not fire a request
  // per page.
  useEffect(() => {
    if (!paper?.pdf_path) return;
    const timer = window.setTimeout(async () => {
      try {
        const d = await api.pdfPage(paperId, visiblePage);
        const page = d.pages[0];
        if (page) setPageText({ page: page.page, text: page.text, count: d.page_count });
      } catch {
        setPageText(null);
      }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [paperId, visiblePage, paper?.pdf_path]);

  const onReveal = useCallback(
    (r: { page: number; rects: { x: number; y: number; w: number; h: number }[] }) => {
      // A token forces a fresh flash even when the same passage is shown twice.
      setReveal({ ...r, token: Date.now() });
    },
    [],
  );

  /** Scroll a stored highlight into view without repainting over it. */
  const goToHighlight = useCallback((h: Highlight) => {
    setActiveHighlight(h.id);
    setReveal({ page: h.page, rects: h.rects, token: Date.now(), flash: false });
  }, []);

  const reloadHighlights = useCallback(async () => {
    try {
      setHighlights((await api.highlights(paperId)).highlights);
    } catch {
      /* the mark is already drawn locally; a failed refresh is not fatal */
    }
  }, [paperId]);

  async function saveNote() {
    try {
      await api.patchPaper(paperId, { note });
      setSavedAt(Date.now());
      notifyChange({ kind: "note", id: paperId, source: "reader" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      await api.uploadPdf(paperId, file);
      setPaper(await api.paper(paperId));
      setOptions(await api.pdfOptions(paperId));
      notifyChange({ kind: "paper", id: paperId, source: "reader" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  async function highlight(comment?: string, color = COLORS[0]) {
    if (!selection) return;
    try {
      const created = await api.createHighlight(paperId, {
        page: selection.page,
        rects: selection.rects,
        quoted: selection.text,
        comment: comment ?? null,
        color,
        page_width: selection.pageWidth,
        page_height: selection.pageHeight,
      });
      setHighlights((h) => [...h, created]);
      setActiveHighlight(created.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      chooseSelection(null);
      setNoteDraft(null);
      window.getSelection()?.removeAllRanges();
    }
  }

  /** Attach the passage to the conversation without typing anything. */
  function addSelectionToContext() {
    if (!selection) return;
    setAttachment({ page: selection.page, text: selection.text });
    // Bring the conversation forward, so the passage lands somewhere visible.
    setSideTab("chat");
    chooseSelection(null);
    window.getSelection()?.removeAllRanges();
  }

  if (error && !paper) return <div className="rd-error">{error}</div>;
  if (!paper) return <div className="rd-loading">Loading…</div>;

  const chatContext: ChatContext = {
    tab: "reader",
    paper: { id: paper.id, title: paper.title, year: paper.year },
    reading:
      pageText && paper.pdf_path
        ? {
            page: pageText.page,
            page_count: pageText.count,
            text: pageText.text,
            outline,
          }
        : null,
  };

  return (
    <div className="rd-root">
      <header className="rd-bar">
        <div className="rd-ident">
          <h1>{paper.title}</h1>
          <p>
            {[paper.authors?.map((a) => a.name).join(", "), paper.year, paper.venue]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {paper.pdf_path && (
          <button
            className={`rd-toggle${dark ? " is-on" : ""}`}
            onClick={() => setDark((v) => !v)}
            title="Invert the page. Figures invert too, so turn it off to read them."
          >
            {dark ? "Inverted" : "Invert"}
          </button>
        )}
        <select
          value={paper.status}
          onChange={async (e) => {
            const status = e.target.value;
            setPaper({ ...paper, status });
            await api.patchPaper(paperId, { status });
            notifyChange({ kind: "status", id: paperId, source: "reader" });
          }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
      </header>

      {error && <div className="rd-error">{error}</div>}

      <div className="rd-split">
        <div className={`rd-pdf${dark ? " is-dark" : ""}`}>
          {paper.pdf_path ? (
            <PdfView
              file={api.pdfUrl(paperId)}
              highlights={highlights}
              activeHighlightId={activeHighlight}
              onSelect={chooseSelection}
              onHighlightClick={setActiveHighlight}
              onVisiblePage={setVisiblePage}
              reveal={reveal}
            />
          ) : (
            <div className="rd-nopdf">
              <p>No PDF stored for this paper.</p>
              {options?.note && <p className="dim">{options.note}</p>}
              {!!options?.options.length && (
                <ul>
                  {options.options.map((o) => (
                    <li key={o.kind}>
                      <a href={o.url} target="_blank" rel="noreferrer">
                        {o.label}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
              <label className="rd-upload">
                {uploading ? "Uploading…" : "Attach a PDF you already have"}
                <input
                  type="file"
                  accept="application/pdf"
                  disabled={uploading}
                  onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
                />
              </label>
            </div>
          )}
        </div>

        <Resizer
          side="right"
          width={sideWidth}
          onChange={setSideWidth}
          min={300}
          max={900}
        />

        <aside className="rd-side" style={{ flexBasis: sideWidth }}>
          <div className="rd-tabs">
            <button
              className={sideTab === "notes" ? "is-active" : ""}
              onClick={() => setSideTab("notes")}
            >
              Notes
            </button>
            <button
              className={sideTab === "chat" ? "is-active" : ""}
              onClick={() => setSideTab("chat")}
            >
              Bread
            </button>
          </div>

          {sideTab === "chat" ? (
            <div className="rd-chat">
              <Assistant
                open
                docked
                onOpenChange={() => undefined}
                context={chatContext}
                attachment={attachment}
                onClearAttachment={() => setAttachment(null)}
                onReveal={onReveal}
                onChanged={() => setVersion((v) => v + 1)}
              />
            </div>
          ) : (
          <div className="rd-notes-scroll">
          <h2>Notes</h2>
          <AutoTextarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={saveNote}
            placeholder="Your notes on this paper…"
            minRows={3}
            maxRows={16}
          />
          <div className="rd-saved">{savedAt ? "Saved" : "Saves when you click away"}</div>

          {!!outline.length && (
            <>
              <h2>Sections</h2>
              <ul className="rd-outline">
                {outline.map((entry, i) => (
                  <li key={i} style={{ paddingLeft: (entry.level - 1) * 11 }}>
                    <button onClick={() => onReveal({ page: entry.page, rects: [] })}>
                      <span className="rd-outline-title">{entry.title}</span>
                      <span className="rd-outline-page">{entry.page}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          <h2>
            Highlights
            <span className="rd-badge">{highlights.length}</span>
          </h2>
          {highlights.length ? (
            <ul className="rd-highlights">
              {highlights.map((h) => (
                <li
                  key={h.id}
                  className={h.id === activeHighlight ? "is-active" : ""}
                  onClick={() => goToHighlight(h)}
                  title="Jump to this passage"
                >
                  <div className="rd-hl-head">
                    <span className="rd-hl-swatch" style={{ background: h.color ?? "#fde047" }} />
                    <span className="rd-hl-page">p{h.page}</span>
                    {h.source === "assistant" && <span className="rd-hl-who">Bread</span>}
                    <button
                      className="rd-hl-del"
                      onClick={async (e) => {
                        e.stopPropagation();
                        await api.deleteHighlight(h.id);
                        void reloadHighlights();
                      }}
                      title="Delete this highlight"
                    >
                      ×
                    </button>
                  </div>
                  {h.quoted ? (
                    <blockquote>{h.quoted}</blockquote>
                  ) : (
                    // A box drawn on a scanned page. There is no text to quote,
                    // so say what it is rather than leaving the entry looking
                    // like a highlight that failed to capture anything.
                    <p className="rd-hl-region">Marked area — no text on this page</p>
                  )}
                  <AutoTextarea
                    className="rd-hl-note"
                    defaultValue={h.comment ?? ""}
                    placeholder="Add a note…"
                    onClick={(e) => e.stopPropagation()}
                    onBlur={async (e) => {
                      if (e.target.value === (h.comment ?? "")) return;
                      await api.updateHighlight(h.id, { comment: e.target.value });
                      void reloadHighlights();
                    }}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="rd-empty">
              Select text in the document to highlight it, note it, or ask Bread about it.
            </p>
          )}

          {/* The abstract is a stand-in for the paper. With the document open
              beside it, it would just be the first paragraph twice. */}
          {!paper.pdf_path && !!paper.abstract && (
            <>
              <h2>Abstract</h2>
              <Abstract className="rd-abstract" text={paper.abstract} />
            </>
          )}
          </div>
          )}
        </aside>
      </div>

      {selection && (
        // Anchored under the selection, so the choice is where you are looking.
        <div
          className="rd-popover"
          data-keep-selection
          style={{ left: selection.anchor.x, top: selection.anchor.y + 8 }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {noteDraft === null ? (
            <>
              <div className="rd-colors">
                {COLORS.map((c) => (
                  <button
                    key={c}
                    className="rd-color"
                    style={{ background: c }}
                    onClick={() => highlight(undefined, c)}
                    title="Highlight"
                  />
                ))}
              </div>
              <button onClick={() => setNoteDraft("")}>Note</button>
              {/* A drawn box carries no text, so there is nothing to hand the
                  assistant. Offering it would attach an empty passage. */}
              {selection.kind !== "region" && (
                <button onClick={addSelectionToContext}>Add to context</button>
              )}
              <button
                onClick={() => {
                  chooseSelection(null);
                  window.getSelection()?.removeAllRanges();
                }}
              >
                ×
              </button>
            </>
          ) : (
            <div className="rd-note-draft">
              <AutoTextarea
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void highlight(noteDraft || undefined);
                  }
                  if (e.key === "Escape") setNoteDraft(null);
                }}
                placeholder="What about this passage?"
                minRows={2}
                maxRows={10}
                autoFocus
              />
              <div className="rd-note-actions">
                <button onClick={() => setNoteDraft(null)}>Cancel</button>
                <button className="primary" onClick={() => highlight(noteDraft || undefined)}>
                  Save note
                </button>
              </div>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
