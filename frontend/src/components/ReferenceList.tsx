import { useEffect, useState } from "react";
import { api, STATIC_MODE } from "../api";
import type { CitedByItem, ReferenceItem } from "../api";
import "./ReferenceList.css";

interface Props {
  paperId: number;
  onPreview: (identifier: string, title: string) => void;
  onOpenPaper?: (paperId: number) => void;
}

/**
 * What a paper cites, and which of your papers cite it.
 *
 * The two tabs are not symmetric, on purpose. References are complete: the
 * whole bibliography is recorded at import as identifiers and titles, whether
 * or not you hold the other side, so the list doubles as the natural place to
 * decide what to add next.
 *
 * "Cited by" is your library only. It is read off the far side of the same
 * reference rows — if a paper you hold lists this one, that row is the edge —
 * so it costs no fetching and stays right as the library grows. The unbounded
 * version of that question, every paper in the world citing this one, is not
 * stored: any cap on tens of thousands of rows is an arbitrary sample, not an
 * answer.
 */
export default function ReferenceList({ paperId, onPreview, onOpenPaper }: Props) {
  const [tab, setTab] = useState<"references" | "cited-by">("references");
  const [items, setItems] = useState<ReferenceItem[] | null>(null);
  const [held, setHeld] = useState(0);
  const [citedBy, setCitedBy] = useState<CitedByItem[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);

  /** Drop a reference the paper does not actually make.
   *
   * Sources fuse records now and then, and a bibliography comes back carrying
   * work the paper never cited. You have read it; nothing here can arbitrate
   * that, so removing the row by hand is the remedy. Only the row goes — a
   * paper you hold on the other end is untouched.
   */
  async function removeReference(item: ReferenceItem) {
    setRemoving(item.id);
    try {
      await api.deleteReference(paperId, item.id);
      setItems((prev) => (prev ?? []).filter((r) => r.id !== item.id));
      if (item.in_library) setHeld((n) => Math.max(0, n - 1));
    } catch {
      // Left in place; the list still shows what the library holds.
    } finally {
      setRemoving(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setCitedBy(null);
    setShowAll(false);
    api
      .references(paperId)
      .then((d) => {
        if (cancelled) return;
        setItems(d.items);
        setHeld(d.in_library);
      })
      .catch(() => !cancelled && setItems([]));
    api
      .citedBy(paperId)
      .then((d) => !cancelled && setCitedBy(d.items))
      .catch(() => !cancelled && setCitedBy([]));
    return () => {
      cancelled = true;
    };
  }, [paperId]);

  const filtered = (items ?? []).filter((i) => !onlyMissing || !i.in_library);
  const rows = tab === "references" ? filtered : (citedBy ?? []);
  const shown = showAll ? rows : rows.slice(0, 12);
  const loading = tab === "references" ? items === null : citedBy === null;

  return (
    <div className="rl-root">
      <div className="rl-tabs">
        <button
          className={tab === "references" ? "is-active" : ""}
          onClick={() => {
            setTab("references");
            setShowAll(false);
          }}
        >
          References
        </button>
        <button
          className={tab === "cited-by" ? "is-active" : ""}
          onClick={() => {
            setTab("cited-by");
            setShowAll(false);
          }}
        >
          Cited by{citedBy?.length ? ` (${citedBy.length})` : ""}
        </button>
        {tab === "references" && items && (
          <span className="rl-count">
            {held} of {items.length} held
          </span>
        )}
      </div>

      {loading ? (
        <p className="rl-empty">Loading…</p>
      ) : !rows.length && !(tab === "references" && onlyMissing) ? (
        <p className="rl-empty">
          {tab === "references"
            ? "No references were recorded for this paper."
            : "No paper in your library cites this one yet."}
        </p>
      ) : (
        <>
          {tab === "references" && (
            <label className="rl-filter">
              <input
                type="checkbox"
                checked={onlyMissing}
                onChange={(e) => {
                  setOnlyMissing(e.target.checked);
                  setShowAll(false);
                }}
              />
              Only ones I do not have
            </label>
          )}

          <ul className="rl-list">
            {tab === "references"
              ? (shown as ReferenceItem[]).map((item) => (
                  <li key={item.id} className={item.in_library ? "is-held" : ""}>
                    <span className="rl-year">{item.year ?? "—"}</span>
                    <span className="rl-body">
                      <span className="rl-title">{item.title ?? item.doi ?? "untitled"}</span>
                      <span className="rl-meta">
                        {[
                          item.authors_blob,
                          item.citation_count != null ? `${item.citation_count} cited` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </span>
                    {item.in_library ? (
                      <button
                        className="rl-action is-held"
                        onClick={() =>
                          item.resolved_paper_id && onOpenPaper?.(item.resolved_paper_id)
                        }
                      >
                        Open
                      </button>
                    ) : STATIC_MODE ? (
                      // Previewing looks a paper up live; there is nothing to
                      // ask. The entry still shows what is cited.
                      <span className="rl-action is-muted">not held</span>
                    ) : (
                      <button
                        className="rl-action"
                        disabled={!item.doi && !item.openalex_id && !item.arxiv_id}
                        onClick={() =>
                          onPreview(
                            (item.doi ?? item.openalex_id ?? item.arxiv_id)!,
                            item.title ?? "",
                          )
                        }
                      >
                        Preview
                      </button>
                    )}
                    {!STATIC_MODE && (
                    <button
                      className="rl-remove"
                      title="Not a reference of this paper — remove it"
                      aria-label={`Remove reference: ${item.title ?? "untitled"}`}
                      disabled={removing === item.id}
                      onClick={() => void removeReference(item)}
                    >
                      ×
                    </button>
                    )}
                  </li>
                ))
              : (shown as CitedByItem[]).map((item) => (
                  <li key={item.id} className="is-held">
                    <span className="rl-year">{item.year ?? "—"}</span>
                    <span className="rl-body">
                      <span className="rl-title">{item.title ?? "untitled"}</span>
                      <span className="rl-meta">
                        {[item.authors_blob, item.venue].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <button className="rl-action is-held" onClick={() => onOpenPaper?.(item.id)}>
                      Open
                    </button>
                  </li>
                ))}
          </ul>

          {rows.length > shown.length && (
            <button className="rl-more" onClick={() => setShowAll(true)}>
              Show all {rows.length}
            </button>
          )}
        </>
      )}
    </div>
  );
}
