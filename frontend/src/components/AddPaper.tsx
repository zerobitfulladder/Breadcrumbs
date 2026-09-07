import { useState } from "react";
import { api, ApiError } from "../api";
import type { SaveResult, Shelf } from "../api";
import { notifyChange } from "../live";
import { usePreviewStream } from "../hooks/usePreviewStream";
import PaperPreviewBody from "./PaperPreviewBody";
import SearchBox from "./SearchBox";
import "./AddPaper.css";

interface Props {
  shelves: Shelf[];
  onSaved: (result: SaveResult) => void;
}

export default function AddPaper({ shelves, onSaved }: Props) {
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SaveResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [shelfIds, setShelfIds] = useState<number[]>([]);

  const s = usePreviewStream();

  function lookup(identifier: string, titleHint?: string) {
    setSaved(null);
    setSaveError(null);
    setShelfIds([]);
    s.lookup(identifier, titleHint);
  }

  async function commit() {
    if (!s.preview) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.save(s.preview.token, shelfIds);
      setSaved(result);
      notifyChange({ kind: "paper", id: result.paper_id, source: "add" });
      s.reset();
      setInput("");
      setShelfIds([]);
      onSaved(result);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const error = saveError ?? s.error;

  return (
    <div className="add-root">
      <div className="add-search">
        <SearchBox
          value={input}
          onChange={setInput}
          onSubmit={lookup}
          disabled={saving}
          busy={s.streaming}
        />
      </div>

      {error && <div className="add-error">{error}</div>}

      {saved && (
        <div className="add-ok">
          <strong>Saved:</strong> {saved.title}
          <div className="add-ok-meta">
            {saved.references_stored} reference{saved.references_stored === 1 ? "" : "s"} recorded
            · {saved.links_created} link{saved.links_created === 1 ? "" : "s"} created
            {saved.pdf_path ? " · PDF downloaded" : ""}
          </div>
        </div>
      )}

      {s.matches && (
        <div className="add-matches">
          <h3>{s.matches.length} matches</h3>
          <ul>
            {s.matches.map((m) => (
              <li key={m.s2_id ?? m.doi ?? m.title}>
                <button
                  onClick={() => {
                    const id = m.doi ?? m.s2_id ?? m.arxiv_id;
                    if (id) lookup(id, m.title);
                  }}
                >
                  <span className="add-match-title">{m.title}</span>
                  <span className="add-match-meta">
                    {[m.authors?.slice(0, 3).map((a) => a.name).join(", "), m.year, m.venue]
                      .filter(Boolean)
                      .join(" · ")}
                    {m.in_library && <span className="sb-badge">in library</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {s.order.length > 0 && (
        <>
          <PaperPreviewBody
            order={s.order}
            panels={s.panels}
            merged={s.merged}
            versions={s.versions}
            versionsError={s.versionsError}
            refs={s.refs}
            preview={s.preview}
            streaming={s.streaming}
            disabled={saving}
            onRetry={s.retry}
            onUseVersion={lookup}
            actions={
              <button
                className="primary"
                onClick={commit}
                disabled={!s.preview || saving || s.streaming}
              >
                {saving
                  ? "Saving…"
                  : s.streaming
                    ? "Fetching…"
                    : s.preview?.already_in_library
                      ? "Update this paper"
                      : "Add to library"}
              </button>
            }
          />

          {shelves.length > 0 && (
            <section className="add-shelves">
              <h3>Add to shelves</h3>
              <div className="add-chips">
                {shelves.map((shelf) => (
                  <button
                    key={shelf.id}
                    className={`chip toggle${shelfIds.includes(shelf.id) ? " is-on" : ""}`}
                    onClick={() =>
                      setShelfIds((v) =>
                        v.includes(shelf.id) ? v.filter((x) => x !== shelf.id) : [...v, shelf.id],
                      )
                    }
                  >
                    {shelf.name}
                  </button>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
