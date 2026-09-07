import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Highlight } from "../api";
import AutoTextarea from "./AutoTextarea";
import Markdown from "./Markdown";
import { notifyChange, onChange } from "../live";
import "./PaperNotes.css";

interface Props {
  paperId: number;
  /** The note already loaded with the paper, so the panel shows it at once. */
  initialNote?: string;
  onOpenReader?: (paperId: number, highlightId?: number) => void;
}

/**
 * A paper's own note and its highlights, editable in place.
 *
 * Double-click to edit rather than a permanent text box: this panel is mostly
 * read while working through the timeline, and an always-live textarea invites
 * changing a note by accident when you meant to select it.
 *
 * Read, a note is rendered as Markdown with its mathematics typeset — notes
 * about papers are full of both, and "$\\eta$ is the learning rate" written in
 * a list is worth reading as one. Editing shows the source, so what you typed
 * is what you get back.
 */
export default function PaperNotes({ paperId, initialNote = "", onOpenReader }: Props) {
  const [note, setNote] = useState(initialNote);
  const [editingNote, setEditingNote] = useState(false);
  const [draft, setDraft] = useState("");
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [editingHighlight, setEditingHighlight] = useState<number | null>(null);
  const [highlightDraft, setHighlightDraft] = useState("");

  const load = useCallback(async () => {
    try {
      const [paper, hl] = await Promise.all([api.paper(paperId), api.highlights(paperId)]);
      setNote(paper.note ?? "");
      setHighlights(hl.highlights);
    } catch {
      /* the panel is a view; a failed refresh leaves what was already shown */
    }
  }, [paperId]);

  useEffect(() => {
    setNote(initialNote);
    setEditingNote(false);
    setEditingHighlight(null);
    void load();
  }, [paperId, initialNote, load]);

  // A highlight added in the reader tab should appear here without a reload.
  useEffect(() => onChange(() => void load()), [load]);

  async function saveNote() {
    setEditingNote(false);
    if (draft === note) return;
    setNote(draft);
    await api.patchPaper(paperId, { note: draft });
    notifyChange({ kind: "note", id: paperId, source: "panel" });
  }

  async function saveHighlightNote(id: number, previous: string) {
    setEditingHighlight(null);
    if (highlightDraft === previous) return;
    setHighlights((all) =>
      all.map((h) => (h.id === id ? { ...h, comment: highlightDraft } : h)),
    );
    await api.updateHighlight(id, { comment: highlightDraft });
    notifyChange({ kind: "note", id: paperId, source: "panel" });
  }

  return (
    <div className="pn-root">
      <h4>
        Note
        {!editingNote && <span className="pn-hint">double-click to edit</span>}
      </h4>
      {editingNote ? (
        <AutoTextarea
          className="pn-editor"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={saveNote}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditingNote(false);
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void saveNote();
          }}
          minRows={4}
          maxRows={24}
          placeholder="Your notes on this paper…"
        />
      ) : (
        <div
          className={`pn-note${note.trim() ? "" : " is-empty"}`}
          onDoubleClick={() => {
            setDraft(note);
            setEditingNote(true);
          }}
          title="Double-click to edit"
        >
          {note.trim() ? (
            <Markdown>{note}</Markdown>
          ) : (
            "No note yet. Double-click to write one."
          )}
        </div>
      )}

      <h4>
        Highlights
        <span className="pn-badge">{highlights.length}</span>
      </h4>
      {highlights.length ? (
        <ul className="pn-highlights">
          {highlights.map((h) => (
            <li key={h.id}>
              <div className="pn-hl-head">
                <span className="pn-swatch" style={{ background: h.color ?? "#fde047" }} />
                <button
                  className="pn-page"
                  onClick={() => onOpenReader?.(paperId, h.id)}
                  title="Open the reader at this passage"
                >
                  p{h.page}
                </button>
                {h.source === "assistant" && <span className="pn-who">Bread</span>}
              </div>
              {h.quoted && <blockquote>{h.quoted}</blockquote>}
              {editingHighlight === h.id ? (
                <AutoTextarea
                  className="pn-editor"
                  value={highlightDraft}
                  onChange={(e) => setHighlightDraft(e.target.value)}
                  onBlur={() => void saveHighlightNote(h.id, h.comment ?? "")}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setEditingHighlight(null);
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void saveHighlightNote(h.id, h.comment ?? "");
                    }
                  }}
                  autoFocus
                  placeholder="What about this passage?"
                />
              ) : (
                <div
                  className={`pn-hl-note${h.comment ? "" : " is-empty"}`}
                  onDoubleClick={() => {
                    setHighlightDraft(h.comment ?? "");
                    setEditingHighlight(h.id);
                  }}
                  title="Double-click to edit"
                >
                  {h.comment ? (
                    <Markdown>{h.comment}</Markdown>
                  ) : (
                    "No note. Double-click to add one."
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="pn-empty">
          Nothing highlighted yet. Open the reader and select text in the document.
        </p>
      )}
    </div>
  );
}
