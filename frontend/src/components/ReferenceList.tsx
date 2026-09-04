import { useEffect, useState } from "react";
import { api } from "../api";
import type { ReferenceItem } from "../api";
import "./ReferenceList.css";

interface Props {
  paperId: number;
  onPreview: (identifier: string, title: string) => void;
  onOpenPaper?: (paperId: number) => void;
}

/**
 * What a paper cites and what cites it.
 *
 * Both lists are complete: every reference is recorded at import as an
 * identifier and a title, whether or not the other side is held. Entries you
 * already have link to your copy; the rest can be previewed, which is what
 * makes this the natural place to decide what to add next.
 */
export default function ReferenceList({ paperId, onPreview, onOpenPaper }: Props) {
  const [direction, setDirection] = useState<"reference" | "citation">("reference");
  const [items, setItems] = useState<ReferenceItem[] | null>(null);
  const [held, setHeld] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [onlyMissing, setOnlyMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setShowAll(false);
    api
      .references(paperId, direction)
      .then((d) => {
        if (cancelled) return;
        setItems(d.items);
        setHeld(d.in_library);
      })
      .catch(() => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, [paperId, direction]);

  const filtered = (items ?? []).filter((i) => !onlyMissing || !i.in_library);
  const shown = showAll ? filtered : filtered.slice(0, 12);

  return (
    <div className="rl-root">
      <div className="rl-tabs">
        <button
          className={direction === "reference" ? "is-active" : ""}
          onClick={() => setDirection("reference")}
        >
          References
        </button>
        <button
          className={direction === "citation" ? "is-active" : ""}
          onClick={() => setDirection("citation")}
        >
          Cited by
        </button>
        {items && (
          <span className="rl-count">
            {held} of {items.length} held
          </span>
        )}
      </div>

      {items === null ? (
        <p className="rl-empty">Loading…</p>
      ) : !items.length ? (
        <p className="rl-empty">
          {direction === "reference"
            ? "No references were recorded for this paper."
            : "No citing papers were recorded."}
        </p>
      ) : (
        <>
          <label className="rl-filter">
            <input
              type="checkbox"
              checked={onlyMissing}
              onChange={(e) => setOnlyMissing(e.target.checked)}
            />
            Only ones I do not have
          </label>

          <ul className="rl-list">
            {shown.map((item) => (
              <li key={item.id} className={item.in_library ? "is-held" : ""}>
                <span className="rl-year">{item.year ?? "—"}</span>
                <span className="rl-body">
                  <span className="rl-title">{item.title ?? item.doi ?? "untitled"}</span>
                  <span className="rl-meta">
                    {[item.authors_blob, item.citation_count != null
                      ? `${item.citation_count} cited`
                      : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                {item.in_library ? (
                  <button
                    className="rl-action is-held"
                    onClick={() => item.resolved_paper_id && onOpenPaper?.(item.resolved_paper_id)}
                  >
                    Open
                  </button>
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
              </li>
            ))}
          </ul>

          {filtered.length > shown.length && (
            <button className="rl-more" onClick={() => setShowAll(true)}>
              Show all {filtered.length}
            </button>
          )}
        </>
      )}
    </div>
  );
}
