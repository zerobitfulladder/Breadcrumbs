import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, STATIC_MODE } from "./api";
import type {
  AuthorRow,
  ChatContext,
  Highlight,
  Paper,
  Shelf,
  TimelinePaper,
  LinkRow,
} from "./api";
import { onChange, notifyChange } from "./live";
import { STATUSES, statusLabel } from "./status";
import AddPaper from "./components/AddPaper";
import About from "./components/About";
import Abstract from "./components/Abstract";
import Markdown from "./components/Markdown";
import Assistant from "./components/Assistant";
import AuthorChip from "./components/AuthorChip";
import AuthorPanel from "./components/AuthorPanel";
import FindPdf from "./components/FindPdf";
import PaperList from "./components/PaperList";
import PaperNotes from "./components/PaperNotes";
import Resizer, { useStoredWidth } from "./components/Resizer";
import PaperPreviewCard from "./components/PaperPreviewCard";
import ReferenceList from "./components/ReferenceList";
import Settings from "./components/Settings";
import Timeline from "./components/Timeline";
import "./App.css";
import CopyChip from "./components/CopyChip";
import AttachPdf from "./components/AttachPdf";
import StarButton from "./components/StarButton";

type Tab = "timeline" | "add" | "settings";

/**
 * A published copy has no backend, so everything that writes is gone: adding
 * papers, settings, the assistant, the reader (there are no PDFs), and every
 * edit control. What remains is the library itself — papers, authors, notes
 * and the links between them — which is the part worth sharing.
 */
const TABS: Tab[] = STATIC_MODE ? ["timeline"] : ["timeline", "add", "settings"];

export default function App() {
  const [tab, setTab] = useState<Tab>("timeline");
  const [papers, setPapers] = useState<TimelinePaper[]>([]);
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [shelves, setShelves] = useState<Shelf[]>([]);
  const [authors, setAuthors] = useState<AuthorRow[]>([]);
  const [selectedAuthorId, setSelectedAuthorId] = useState<number | null>(null);
  const [hoverAuthorId, setHoverAuthorId] = useState<number | null>(null);
  // A click pins a paper; a hover previews one. The hover wins while it lasts,
  // so moving the pointer explores without losing what you pinned.
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [hoverId, setHoverId] = useState<number | null>(null);
  /**
   * Hover coming from the list only.
   *
   * The grid pans a paper into view when this changes, which is what the list
   * needs — you point at a row and the card comes to you. Feeding the grid's
   * own hover back into it made it chase the pointer instead: hovering a
   * half-visible card scrolled it into view under the cursor.
   */
  const [listHoverId, setListHoverId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Paper | null>(null);
  /** The selected paper's marked passages, shown beside it. */
  const [marks, setMarks] = useState<Highlight[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [askDraft, setAskDraft] = useState<string | null>(null);
  /** Bumped on every change, so panels holding their own data refetch. */
  const [dataVersion, setDataVersion] = useState(0);
  const [leftWidth, setLeftWidth] = useStoredWidth("breadcrumbs.panel.left", 264);
  const [rightWidth, setRightWidth] = useStoredWidth("breadcrumbs.panel.right", 384);
  const [previewing, setPreviewing] = useState<{ id: string; title: string } | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);

  // Double-tap Shift toggles the assistant, the way an IDE's quick-search
  // opens. A Shift held as a modifier must not count, so any other key in
  // between cancels the pair.
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
        setAssistantOpen((v) => !v);
      } else {
        lastShift = now;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  /** Reading happens in its own tab, so several papers can stay open at once. */
  const openReader = useCallback((id: number, highlightId?: number) => {
    const query = highlightId ? `?paper=${id}&highlight=${highlightId}` : `?paper=${id}`;
    window.open(`${window.location.pathname}${query}`, "_blank", "noopener");
  }, []);

  const activeId = hoverId ?? selectedId;
  const activeAuthorId = hoverAuthorId ?? selectedAuthorId;

  /**
   * Papers to keep lit. With a paper in focus that is whatever it cites or is
   * cited by; with an author in focus it is everything they appear on.
   */
  const connectedIds = useMemo(() => {
    const out = new Set<number>();
    if (activeAuthorId != null) {
      const author = authors.find((a) => a.id === activeAuthorId);
      author?.paper_ids.forEach((id) => out.add(id));
      return out;
    }
    if (activeId == null) return out;
    for (const l of links) {
      if (l.src_paper_id === activeId) out.add(l.dst_paper_id);
      else if (l.dst_paper_id === activeId) out.add(l.src_paper_id);
    }
    return out;
  }, [links, activeId, activeAuthorId, authors]);

  // Papers and authors are mutually exclusive focuses: picking one clears the
  // other, so the timeline is never lit by two different rules at once.
  /** Authors of the paper in focus, so the author list can mark them. */
  const authorsOfActivePaper = useMemo(() => {
    const out = new Set<number>();
    if (activeId == null) return out;
    for (const a of authors) {
      if (a.paper_ids.includes(activeId)) out.add(a.id);
    }
    return out;
  }, [authors, activeId]);

  const toggle = useCallback((id: number) => {
    setSelectedAuthorId(null);
    setHoverAuthorId(null);
    setSelectedId((current) => (current === id ? null : id));
  }, []);

  /** What the assistant is told about the current view, sent with each message. */
  const chatContext: ChatContext = useMemo(() => {
    const paper = papers.find((p) => p.id === selectedId);
    const author = authors.find((a) => a.id === selectedAuthorId);
    return {
      tab,
      paper: paper ? { id: paper.id, title: paper.title, year: paper.year } : null,
      author: author ? { id: author.id, name: author.name } : null,
      visible_papers: papers.slice(0, 40).map((p) => ({ id: p.id, title: p.title })),
    };
  }, [tab, papers, authors, selectedId, selectedAuthorId]);

  const toggleAuthor = useCallback((id: number) => {
    setSelectedId(null);
    setHoverId(null);
    setSelectedAuthorId((current) => (current === id ? null : id));
  }, []);

  /** From a paper's byline: show the author but keep the paper underneath. */
  const openAuthorFromPaper = useCallback((id: number) => {
    setHoverAuthorId(null);
    setSelectedAuthorId(id);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [tl, sh, au] = await Promise.all([api.timeline(), api.shelves(), api.authors()]);
      setPapers(tl.papers);
      setLinks(tl.links);
      setShelves(sh.shelves);
      setAuthors(au.authors);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  /** Remove a paper as though it had never been added.
   *
   * The backend takes its references, links, highlights and stored PDF, plus
   * any author or institution no other paper still uses. Papers that cite this
   * one keep the reference; it simply goes back to being one you do not hold.
   */
  const removePaper = useCallback(
    async (paperId: number) => {
      setDeleting(true);
      try {
        await api.deletePaper(paperId);
        setConfirmDelete(null);
        setSelectedId((current) => (current === paperId ? null : current));
        setDetail(null);
        await refresh();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setDeleting(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Anything that writes — the assistant here, or a save in the reader tab —
  // announces it, and every view refetches rather than waiting for a reload.
  useEffect(
    () =>
      onChange(() => {
        void refresh();
        setDataVersion((v) => v + 1);
      }),
    [refresh],
  );

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setMarks([]);
      return;
    }
    let cancelled = false;
    api
      .paper(selectedId)
      .then((p) => !cancelled && setDetail(p))
      .catch(() => !cancelled && setDetail(null));
    // Read beside the paper, not only inside the reader. A marked passage is
    // the reading itself, and it is the only trace of it left on a copy with
    // no PDF behind it.
    api
      .highlights(selectedId)
      .then((d) => !cancelled && setMarks(d.highlights))
      .catch(() => !cancelled && setMarks([]));
    return () => {
      cancelled = true;
    };
  }, [selectedId, dataVersion]);

  return (
    <div className="app">
      <header className="app-bar">
        <span className="app-name">Breadcrumbs</span>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={tab === t ? "is-active" : ""} onClick={() => setTab(t)}>
              {t === "add" ? "Add paper" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
        <span className="app-count">{papers.length} in library</span>
{!STATIC_MODE && (
        <button
          className="app-bread"
          onClick={() => setAssistantOpen((v) => !v)}
          title="Open Bread, the assistant"
        >
          <kbd>⇧⇧</kbd> for Bread
        </button>
        )}
        {STATIC_MODE && <About />}
      </header>

      {error && <div className="app-error">{error}</div>}

      <main className="app-main">
        {tab === "timeline" && (
          <div className="app-split">
            <PaperList
              papers={papers}
              authors={authors}
              highlightedAuthorIds={authorsOfActivePaper}
              activeId={activeId}
              selectedId={selectedId}
              connectedIds={connectedIds}
              activeAuthorId={activeAuthorId}
              selectedAuthorId={selectedAuthorId}
              onHover={(id) => {
                setHoverId(id);
                setListHoverId(id);
              }}
              onToggle={toggle}
              onHoverAuthor={setHoverAuthorId}
              onToggleAuthor={toggleAuthor}
              onOpenReader={openReader}
              mapScope={
                selectedAuthorId != null
                  ? "author"
                  : selectedId != null
                    ? "paper"
                    : "library"
              }
              mapId={selectedAuthorId ?? selectedId ?? null}
              refreshKey={dataVersion}
              width={leftWidth}
            />
            <Resizer side="left" width={leftWidth} onChange={setLeftWidth} min={200} max={560} />
            <Timeline
              papers={papers}
              links={links}
              activeId={activeAuthorId != null ? null : activeId}
              selectedId={selectedId}
              connectedIds={connectedIds}
              focusMode={activeId != null || activeAuthorId != null}
              focusId={listHoverId}
              panelOpen={selectedAuthorId != null || detail != null}
              onHover={setHoverId}
              onToggle={toggle}
              onOpenReader={openReader}
              onClearSelection={() => setSelectedId(null)}
            />
            {(selectedAuthorId != null || detail) && (
              <Resizer
                side="right"
                width={rightWidth}
                onChange={setRightWidth}
                min={280}
                max={760}
              />
            )}
            {selectedAuthorId != null && (
              <AuthorPanel
                authorId={selectedAuthorId}
                refreshKey={dataVersion}
                onSelectPaper={toggle}
                onAsk={setAskDraft}
                backLabel={
                  selectedId != null
                    ? papers.find((p) => p.id === selectedId)?.title ?? "the paper"
                    : undefined
                }
                onBack={selectedId != null ? () => setSelectedAuthorId(null) : undefined}
                width={rightWidth}
              />
            )}
            {selectedAuthorId == null && detail && (
              <aside className="app-detail" style={{ flexBasis: rightWidth }}>
                <div className="app-detail-head">
                  <h2>{detail.title}</h2>
                  <StarButton kind="paper" id={detail.id} starred={!!detail.favorite} />
                </div>
                <p className="dim">
                  {[detail.year, detail.venue].filter(Boolean).join(" · ")}
                  {detail.pdf_path && (
                    <span className="app-pdf-badge" title="PDF stored locally">
                      PDF
                    </span>
                  )}
                </p>
                <div className="app-ids">
                  {detail.doi ? (
                    <CopyChip
                      value={detail.doi}
                      prefix="DOI"
                      title={`Copy ${detail.doi}`}
                    />
                  ) : (
                    // Said out loud, so a missing chip reads as a fact about
                    // the paper rather than a missing feature.
                    <span className="app-noid">No DOI on record</span>
                  )}
                  {detail.arxiv_id && (
                    <CopyChip
                      value={detail.arxiv_id}
                      prefix="arXiv"
                      title={`Copy ${detail.arxiv_id}`}
                    />
                  )}
                </div>

                <label className="app-status">
                  <span>Status</span>
                  <select
                    disabled={STATIC_MODE}
                    value={detail.status}
                    onChange={async (e) => {
                      const status = e.target.value;
                      setDetail({ ...detail, status });
                      await api.patchPaper(detail.id, { status });
                      notifyChange({ kind: "status", id: detail.id, source: "detail" });
                    }}
                  >
                    {STATUSES.map((st) => (
                      <option key={st} value={st}>
                        {statusLabel(st)}
                      </option>
                    ))}
                  </select>
                </label>

                <h4>Authors</h4>
                <ul className="app-authors">
                  {(detail.authors ?? []).map((a) => (
                    <li key={a.id ?? a.name}>
                      <AuthorChip author={a} onOpen={openAuthorFromPaper} />
                    </li>
                  ))}
                </ul>
                {/* The reader is the PDF; a published copy has none. */}
                {!STATIC_MODE && (
                  <button className="app-open-reader" onClick={() => openReader(detail.id)}>
                    Open in reader
                  </button>
                )}
                {detail.pdf_path && (
                  <a className="app-pdf" href={api.pdfUrl(detail.id)} target="_blank" rel="noreferrer">
                    Open PDF
                  </a>
                )}
                {!STATIC_MODE && (
                  <>
                    <FindPdf paperId={detail.id} hasPdf={!!detail.pdf_path} />
                    <AttachPdf paperId={detail.id} hasPdf={!!detail.pdf_path} />
                  </>
                )}
                {/* The note is still shown — it is the reading, and the point
                    of publishing — but only as text once nothing can save it. */}
                {STATIC_MODE ? (
                  detail.note ? (
                    <div className="app-note-ro">
                      <Markdown>{detail.note}</Markdown>
                    </div>
                  ) : null
                ) : (
                  <PaperNotes
                    paperId={detail.id}
                    initialNote={detail.note ?? ""}
                    onOpenReader={openReader}
                  />
                )}

                {!!detail.abstract && (
                  <details className="app-abstract">
                    <summary>Abstract</summary>
                    <Abstract text={detail.abstract} />
                  </details>
                )}

                {marks.length > 0 && (
                  <div className="app-marks">
                    <h4>
                      Marked passages <span className="app-marks-n">{marks.length}</span>
                    </h4>
                    <ul>
                      {marks.map((m) => (
                        <li
                          key={m.id}
                          // Clicking opens the reader at that passage. Without a
                          // PDF there is nowhere to go, so it stays plain text.
                          className={STATIC_MODE ? "" : "is-clickable"}
                          onClick={
                            STATIC_MODE ? undefined : () => openReader(detail.id, m.id)
                          }
                        >
                          <span className="app-mark-page" style={{ background: m.color ?? "#fcee0c" }}>
                            p{m.page}
                          </span>
                          <span className="app-mark-body">
                            {m.quoted && <q>{m.quoted}</q>}
                            {m.comment && (
                              <div className="app-mark-note">
                                <Markdown>{m.comment}</Markdown>
                              </div>
                            )}
                            {!m.quoted && !m.comment && (
                              <span className="app-mark-note">Marked area</span>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <ReferenceList
                  paperId={detail.id}
                  onPreview={(id, title) => setPreviewing({ id, title })}
                  onOpenPaper={toggle}
                />

                {!STATIC_MODE && (
                <div className="app-danger">
                  {confirmDelete === detail.id ? (
                    <>
                      <p className="app-danger-note">
                        Remove this paper, its references, links, highlights and stored PDF,
                        and any author no other paper credits. Papers that cite it keep the
                        reference. This cannot be undone.
                      </p>
                      <div className="app-danger-row">
                        <button
                          className="app-danger-go"
                          disabled={deleting}
                          onClick={() => void removePaper(detail.id)}
                        >
                          {deleting ? "Deleting…" : "Delete permanently"}
                        </button>
                        <button disabled={deleting} onClick={() => setConfirmDelete(null)}>
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <button
                      className="app-danger-open"
                      onClick={() => setConfirmDelete(detail.id)}
                    >
                      Delete this paper
                    </button>
                  )}
                </div>
                )}
              </aside>
            )}
          </div>
        )}
        {tab === "add" && <AddPaper shelves={shelves} onSaved={() => void refresh()} />}
        {tab === "settings" && <Settings />}
      </main>

      {previewing && (
        <PaperPreviewCard
          identifier={previewing.id}
          titleHint={previewing.title}
          onClose={() => setPreviewing(null)}
          onSaved={() => void refresh()}
        />
      )}

      {!STATIC_MODE && (
      <Assistant
        open={assistantOpen}
        onOpenChange={setAssistantOpen}
        context={chatContext}
        draft={askDraft}
        onDraftUsed={() => setAskDraft(null)}
        onChanged={() => void refresh()}
      />
      )}
    </div>
  );
}
