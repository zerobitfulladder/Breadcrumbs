import { useMemo, useState } from "react";
import type { AuthorRow, TimelinePaper } from "../api";
import AffiliationMap from "./AffiliationMap";
import "./PaperList.css";
import StarButton from "./StarButton";

interface Props {
  papers: TimelinePaper[];
  authors: AuthorRow[];
  activeId?: number | null;
  selectedId?: number | null;
  connectedIds?: Set<number>;
  activeAuthorId?: number | null;
  selectedAuthorId?: number | null;
  onHover?: (id: number | null) => void;
  onToggle?: (id: number) => void;
  onHoverAuthor?: (id: number | null) => void;
  onToggleAuthor?: (id: number) => void;
  onOpenReader?: (id: number) => void;
  /** The map below the list follows whatever is in focus. */
  /** Authors of the paper currently in focus, marked in the author list. */
  highlightedAuthorIds?: Set<number>;
  mapScope?: "library" | "author" | "paper";
  mapId?: number | null;
  refreshKey?: number;
  /** Set by the divider beside it; persisted by the caller. */
  width?: number;
}

type SortKey = "year" | "title" | "cites";
type Dir = "asc" | "desc";
type Mode = "papers" | "authors";

/** Which way a column reads first. Numbers open on the largest, names on A. */
const FIRST_DIR: Record<SortKey, Dir> = { year: "desc", title: "asc", cites: "desc" };

/**
 * Favourites first, then whatever the columns say.
 *
 * A starred paper is one the reader singled out, and hunting for it in a list
 * of a hundred and forty defeats the point of having starred it. So the star
 * outranks the sort rather than participating in it: the two groups are ordered
 * by the chosen column independently, and flipping a column reorders inside
 * each without moving anything across the line between them.
 */
function favouritesFirst<T extends { favorite?: number }>(
  rows: T[],
  compare: (a: T, b: T) => number,
): T[] {
  return [...rows].sort(
    (a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0) || compare(a, b),
  );
}

export default function PaperList({
  papers,
  authors,
  activeId = null,
  selectedId = null,
  connectedIds,
  activeAuthorId = null,
  selectedAuthorId = null,
  onHover,
  onToggle,
  onHoverAuthor,
  onToggleAuthor,
  onOpenReader,
  highlightedAuthorIds,
  mapScope = "library",
  mapId = null,
  refreshKey = 0,
  width,
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [mode, setMode] = useState<Mode>("papers");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("year");
  const [dir, setDir] = useState<Dir>(FIRST_DIR.year);

  /** Same column flips; a new one opens the way that column reads first. */
  function sortBy(key: SortKey) {
    if (key === sort) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(key);
      setDir(FIRST_DIR[key]);
    }
  }

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? papers.filter(
          (p) =>
            p.title.toLowerCase().includes(q) ||
            p.authors.some((a) => a.toLowerCase().includes(q)) ||
            String(p.year).includes(q),
        )
      : papers;

    // The direction flips the chosen column only. Title stays the tie-break in
    // reading order either way, so equal years never shuffle when you flip.
    const flip = dir === "desc" ? -1 : 1;
    return favouritesFirst(filtered, (a, b) => {
      if (sort === "title") return flip * a.title.localeCompare(b.title);
      if (sort === "cites")
        return (
          flip * ((a.citation_count ?? 0) - (b.citation_count ?? 0)) ||
          a.title.localeCompare(b.title)
        );
      return flip * ((a.year ?? 0) - (b.year ?? 0)) || a.title.localeCompare(b.title);
    });
  }, [papers, query, sort, dir]);

  const shownAuthors = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? authors.filter(
          (a) =>
            a.name.toLowerCase().includes(q) ||
            (a.affiliation ?? "").toLowerCase().includes(q),
        )
      : authors;

    const flip = dir === "desc" ? -1 : 1;
    return favouritesFirst(filtered, (a, b) => {
      if (sort === "title") return flip * a.name.localeCompare(b.name);
      if (sort === "cites")
        return flip * (a.paper_count - b.paper_count) || a.name.localeCompare(b.name);
      return flip * ((a.first_year ?? 0) - (b.first_year ?? 0)) || a.name.localeCompare(b.name);
    });
  }, [authors, query, sort, dir]);

  /*
   * Where the line between the groups falls: the index of the last favourite,
   * and only when there is something on the other side of it. A list that is
   * all favourites, or none, gets no rule — a separator with nothing under it
   * is a line for its own sake.
   */
  const favEdge = (rows: { favorite?: number }[]) => {
    const favourites = rows.filter((r) => r.favorite).length;
    return favourites && favourites < rows.length ? favourites - 1 : -1;
  };
  const paperEdge = favEdge(shown);
  const authorEdge = favEdge(shownAuthors);

  if (collapsed) {
    return (
      <div className="pl-root is-collapsed">
        <button
          className="pl-toggle"
          onClick={() => setCollapsed(false)}
          title="Show the paper list"
          aria-label="Show the paper list"
        >
          <span className="pl-toggle-icon">›</span>
          <span className="pl-toggle-label">
            {mode === "papers" ? `Papers (${papers.length})` : `Authors (${authors.length})`}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="pl-root" style={width ? { flexBasis: width } : undefined}>
      <div className="pl-head">
        <div className="pl-modes">
          <button
            className={mode === "papers" ? "is-active" : ""}
            onClick={() => setMode("papers")}
          >
            Papers
          </button>
          <button
            className={mode === "authors" ? "is-active" : ""}
            onClick={() => setMode("authors")}
          >
            Authors
          </button>
        </div>
        <span className="pl-count">
          {mode === "papers" ? shown.length : shownAuthors.length}
        </span>
        <button
          className="pl-collapse"
          onClick={() => setCollapsed(true)}
          title="Collapse the list"
          aria-label="Collapse the list"
        >
          ‹
        </button>
      </div>

      <div className="pl-controls">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={mode === "papers" ? "Filter by title or author" : "Filter by name"}
          spellCheck={false}
        />
        <div className="pl-sorts">
          {(["year", "title", "cites"] as SortKey[]).map((k) => {
            const label =
              mode === "papers"
                ? k === "cites" ? "cited" : k
                : k === "year" ? "first" : k === "title" ? "name" : "papers";
            const active = sort === k;
            return (
              <button
                key={k}
                className={active ? "is-active" : ""}
                onClick={() => sortBy(k)}
                title={
                  active
                    ? `Sorted ${dir === "asc" ? "ascending" : "descending"} — click to reverse`
                    : `Sort by ${label}`
                }
              >
                {label}
                {active && <span className="pl-arrow">{dir === "asc" ? "↑" : "↓"}</span>}
              </button>
            );
          })}
        </div>
      </div>

      {mode === "authors" ? (
        <ul className="pl-list" onPointerLeave={() => onHoverAuthor?.(null)}>
          {shownAuthors.map((a, i) => (
            <li key={a.id} className={`pl-row${i === authorEdge ? " is-fav-edge" : ""}`}>
              <button
                className={[
                  "pl-item",
                  a.id === activeAuthorId ? "is-active" : "",
                  a.id === selectedAuthorId ? "is-pinned" : "",
                  highlightedAuthorIds?.has(a.id) ? "is-linked" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onPointerEnter={() => onHoverAuthor?.(a.id)}
                onFocus={() => onHoverAuthor?.(a.id)}
                onClick={() => onToggleAuthor?.(a.id)}
              >
                <span className="pl-item-year">{a.paper_count}</span>
                <span className="pl-item-body">
                  <span className="pl-item-title">{a.name}</span>
                  <span className="pl-item-meta">
                    {[
                      a.affiliation,
                      a.first_year === a.last_year
                        ? a.first_year
                        : `${a.first_year}–${a.last_year}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
              </button>
              <StarButton kind="author" id={a.id} starred={!!a.favorite} compact />
            </li>
          ))}
          {!shownAuthors.length && <li className="pl-empty">No authors match that filter.</li>}
        </ul>
      ) : (
      <ul className="pl-list" onPointerLeave={() => onHover?.(null)}>
        {shown.map((p, i) => {
          const isActive = p.id === activeId;
          const isLinked = connectedIds?.has(p.id) ?? false;
          return (
            <li key={p.id} className={`pl-row${i === paperEdge ? " is-fav-edge" : ""}`}>
              <button
                className={[
                  "pl-item",
                  isActive ? "is-active" : "",
                  isLinked ? "is-linked" : "",
                  p.id === selectedId ? "is-pinned" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onPointerEnter={() => onHover?.(p.id)}
                onFocus={() => onHover?.(p.id)}
                onClick={() => onToggle?.(p.id)}
                onAuxClick={(e) => {
                  if (e.button !== 1) return;
                  e.preventDefault();
                  onOpenReader?.(p.id);
                }}
                onMouseDown={(e) => e.button === 1 && e.preventDefault()}
              >
                <span className="pl-item-year">{p.year ?? "—"}</span>
                <span className="pl-item-body">
                  <span className="pl-item-title">{p.title}</span>
                  <span className="pl-item-meta">
                    {p.authors[0]?.split(" ").slice(-1)[0] ?? "?"}
                    {p.authors.length > 1 ? " et al." : ""}
                    {p.citation_count != null ? ` · ${p.citation_count} cited` : ""}
                  </span>
                </span>
              </button>
              <StarButton kind="paper" id={p.id} starred={!!p.favorite} compact />
            </li>
          );
        })}
        {!shown.length && <li className="pl-empty">Nothing matches that filter.</li>}
      </ul>
      )}

      <div className="pl-map">
        <AffiliationMap scope={mapScope} id={mapId} refreshKey={refreshKey} />
      </div>
    </div>
  );
}
