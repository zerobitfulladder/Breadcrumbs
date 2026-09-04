import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LinkRow, TimelinePaper } from "../api";
import { statusLabel } from "../status";
import {
  DEFAULT_BIN_YEARS, GAP, HEADER_H, MAX_BIN_YEARS, MIN_BIN_YEARS, PAD, binnedLayout,
  cardSize, cardText,
} from "./timelineLayout";
import "./Timeline.css";

interface Props {
  papers: TimelinePaper[];
  links?: LinkRow[];
  /** Hovered or selected: the paper whose connections are on show. */
  activeId?: number | null;
  /** Pinned by a click, so the highlight survives the pointer leaving. */
  selectedId?: number | null;
  /** Ids linked to activeId, in either direction. */
  connectedIds?: Set<number>;
  /** Dim unrelated papers even when no single paper is active (author focus). */
  focusMode?: boolean;
  /** Pan this paper into view when it changes (used by the list sidebar). */
  focusId?: number | null;
  onHover?: (id: number | null) => void;
  onToggle?: (id: number) => void;
  onOpenReader?: (id: number) => void;
  onClearSelection?: () => void;
}

/** Pointer movement under this counts as a click, not a drag. */
const CLICK_SLOP = 4;
const MIN_ZOOM = 0.4;
const MAX_ZOOM = 6;
const VIEW_KEY = "breadcrumbs.grid.view";

interface SavedView {
  x: number;
  y: number;
  zoom: number;
  binYears: number;
}

function loadView(): SavedView | null {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as SavedView;
    if (!Number.isFinite(v.x) || !Number.isFinite(v.y) || !(v.zoom > 0)) return null;
    // `??` at the use site only catches null and undefined, so a stored NaN or
    // an out-of-range span would reach the layout intact. Anything unusable
    // falls back to the default rather than restoring a broken view.
    const span = Math.round(v.binYears);
    return {
      ...v,
      binYears:
        Number.isFinite(span) && span >= MIN_BIN_YEARS && span <= MAX_BIN_YEARS
          ? span
          : DEFAULT_BIN_YEARS,
    };
  } catch {
    return null;   // a blocked or corrupt store just means the default view
  }
}

const KIND_CLASS: Record<string, string> = {
  topic: "kind-topic",
  supplement: "kind-supplement",
  dataset: "kind-dataset",
  background: "kind-background",
  method: "kind-method",
};

export default function Timeline({
  papers,
  links = [],
  activeId = null,
  selectedId = null,
  connectedIds,
  focusMode: focusModeProp,
  focusId = null,
  onHover,
  onToggle,
  onOpenReader,
  onClearSelection,
}: Props) {
  const saved = useRef(loadView());
  const hostRef = useRef<HTMLDivElement>(null);

  const [size, setSize] = useState({ w: 900, h: 560 });

  /**
   * Pan and zoom together, in one piece of state.
   *
   * Zooming has to move the pan in the same step — the point under the cursor
   * stays put only if both change at once. Held apart, that meant calling one
   * setter inside the other's updater, and React is free to run an updater more
   * than once, which applied the anchor shift twice and drifted the view.
   */
  const [view, setView] = useState(() => ({
    x: saved.current?.x ?? 0,
    y: saved.current?.y ?? 0,
    zoom: saved.current?.zoom ?? 1,
  }));
  const { x: offsetX, y: offsetY, zoom } = view;
  const [binYears, setBinYears] = useState(
    () => saved.current?.binYears ?? DEFAULT_BIN_YEARS,
  );
  const [dragging, setDragging] = useState(false);

  const dated = useMemo(() => papers.filter((p) => p.year != null), [papers]);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      setSize({ w: box.width, h: box.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Window size, which a side panel does not change. The canvas size does.
  const [windowSize, setWindowSize] = useState(() => ({
    w: typeof window === "undefined" ? 0 : window.innerWidth,
    h: typeof window === "undefined" ? 0 : window.innerHeight,
  }));
  useEffect(() => {
    const measure = () =>
      setWindowSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  /**
   * The area the grid is sized against, sampled and then held.
   *
   * Not the live canvas size. Selecting a paper opens the detail panel, which
   * takes a few hundred pixels off the canvas; re-solving there would resize
   * every card the moment you clicked one. So the area is taken when the
   * library changes, when the bin span changes, or when the window itself
   * resizes. The panel covers the right of the grid; panning reaches what it
   * hides.
   */
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (size.w < 50) return;
    setBox({ w: size.w, h: size.h });
    // `size` is deliberately absent: sampling on every canvas change is the
    // resize this exists to prevent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dated, binYears, windowSize]);

  const grid = useMemo(
    () => binnedLayout(dated, box.w || size.w, box.h || size.h, binYears, zoom),
    // Same reason: the sampled area is the input, not the live canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dated, box, binYears, zoom],
  );

  const boxById = useMemo(
    () => new Map(grid.boxes.map((b) => [b.paper.id, b])),
    [grid],
  );

  // Every card is the same size, so the type is measured once and handed to
  // them all as custom properties rather than computed per card.
  const text = useMemo(() => {
    const first = grid.boxes[0];
    return cardText(first?.w ?? 0, first?.h ?? 0);
  }, [grid]);

  // Remember where they are looking. Written on a timer so a drag does not hit
  // localStorage on every frame.
  const latest = useRef({ x: offsetX, y: offsetY, zoom, binYears });
  latest.current = { x: offsetX, y: offsetY, zoom, binYears };

  const store = useCallback(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(latest.current));
    } catch {
      /* not remembering the view is not worth surfacing */
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(store, 250);
    return () => window.clearTimeout(timer);
  }, [offsetX, offsetY, zoom, binYears, store]);

  // The debounce is cancelled on unmount too, so the last pan of a session
  // would otherwise never be written. Flush it, and on the way out of the tab.
  useEffect(() => {
    window.addEventListener("pagehide", store);
    return () => {
      window.removeEventListener("pagehide", store);
      store();
    };
  }, [store]);

  // Bound once, so the wheel handler reads the live layout from here.
  const anchorRef = useRef({ cols: 1, rows: 1, w: 0, h: 0 });
  anchorRef.current = { cols: grid.cols, rows: grid.rows, w: box.w || size.w, h: box.h || size.h };

  // --- zoom, anchored under the pointer --------------------------------------
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      setView((v) => {
        const next = Math.min(
          MAX_ZOOM,
          Math.max(MIN_ZOOM, v.zoom * Math.pow(0.999, e.deltaY)),
        );
        if (next === v.zoom) return v;

        // Hold whatever is under the cursor still while the scale changes.
        //
        // Zoom cannot simply be divided out of the position: a card sits at
        // PAD + col * (w + GAP), and only `w` scales — the padding and the
        // gutters do not. Treating the layout as a plain scale therefore drifts
        // the anchor, badly at high column counts. So the point under the
        // cursor is converted to a fractional column and row, which mean the
        // same thing at any zoom, and placed back afterwards.
        const { cols, rows, w: areaW, h: areaH } = anchorRef.current;
        const cur = cardSize(cols, rows, areaW, areaH, v.zoom);
        const now = cardSize(cols, rows, areaW, areaH, next);
        const col = (px - v.x - PAD) / (cur.w + GAP);
        const row = (py - v.y - PAD - HEADER_H) / (cur.h + GAP);
        return {
          zoom: next,
          x: px - (PAD + col * (now.w + GAP)),
          y: py - (PAD + HEADER_H + row * (now.h + GAP)),
        };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // --- pan --------------------------------------------------------------------
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: number } | null>(
    null,
  );

  /**
   * The card the press started on, if any.
   *
   * Panning has to work from anywhere — the grid is mostly cards, so requiring
   * background would make it nearly undraggable. But the canvas takes pointer
   * capture to pan, and a capture retargets the click away from the card, so
   * the card can never receive one. The press is recorded here instead and
   * resolved on release, which is also what tells a click from a drag.
   */
  const pressedOn = useRef<number | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, ox: offsetX, oy: offsetY, moved: 0 };
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    d.moved = Math.max(d.moved, Math.abs(dx) + Math.abs(dy));
    setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }));
  };
  const endDrag = () => {
    const d = drag.current;
    const card = pressedOn.current;
    drag.current = null;
    pressedOn.current = null;
    setDragging(false);
    if (!d || d.moved >= CLICK_SLOP) return;   // it was a pan
    // A click on a card selects it; on the background it clears the pin.
    if (card != null) onToggle?.(card);
    else onClearSelection?.();
  };

  // --- bring a paper into view when the list asks for it ----------------------
  const panned = useRef<number | null>(null);
  useEffect(() => {
    if (focusId == null) {
      panned.current = null;
      return;
    }
    // Only when the target itself changes. `boxById` has to be read here, but
    // it is rebuilt on every zoom step, and without this guard each wheel tick
    // re-ran this and panned the focused card back into view — overwriting the
    // anchor that keeps the cursor position fixed.
    if (panned.current === focusId) return;
    const b = boxById.get(focusId);
    if (!b) return;
    panned.current = focusId;
    const margin = 24;
    const left = b.x + offsetX;
    const top = b.y + offsetY;
    let dx = 0;
    let dy = 0;
    if (left < margin) dx = margin - left;
    else if (left + b.w > size.w - margin) dx = size.w - margin - b.w - left;
    if (top < margin) dy = margin - top;
    else if (top + b.h > size.h - margin) dy = size.h - margin - b.h - top;
    if (dx || dy) setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    // Only react to a change of target, not to every pan the user makes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, boxById]);

  /** Centre of a card, for drawing a link between two of them. */
  const centreOf = useCallback(
    (id: number) => {
      const b = boxById.get(id);
      return b
        ? { x: b.x + offsetX + b.w / 2, y: b.y + offsetY + b.h / 2 }
        : null;
    },
    [boxById, offsetX, offsetY],
  );

  // --- edges ------------------------------------------------------------------
  // Only for the paper in focus. Drawing the whole graph at rest is noise at
  // this density: every card has neighbours, so the lines cover the grid and
  // say nothing about any particular paper.
  const edges = useMemo(() => {
    if (activeId == null) return [];
    return links.flatMap((l) => {
      const cites = l.src_paper_id === activeId;
      const cited = l.dst_paper_id === activeId;
      if (!cites && !cited) return [];
      const a = centreOf(l.src_paper_id);
      const b = centreOf(l.dst_paper_id);
      if (!a || !b) return [];
      return [
        {
          id: l.id,
          // A link means something different depending on which end you are
          // standing on: work this paper drew on, or work that came back to
          // it. One colour for each, so a glance separates them.
          dir: cites ? "ref" : "cite",
          d: `M ${a.x} ${a.y} L ${b.x} ${b.y}`,
        },
      ];
    });
  }, [links, centreOf, activeId]);

  const emptyColumns = useMemo(
    () => grid.columns.filter((c) => c.empty).length,
    [grid],
  );

  const focusMode = focusModeProp ?? activeId != null;
  const linkedCount = connectedIds?.size ?? 0;

  return (
    <div className="tl-root">
      <div className="tl-toolbar">
        <div className="tl-bin-control">
          <button
            onClick={() => setBinYears((y) => Math.max(MIN_BIN_YEARS, y - 1))}
            disabled={binYears <= MIN_BIN_YEARS}
            title="Narrower intervals, so more columns"
          >
            −
          </button>
          <span className="tl-bin-value">
            {binYears} {binYears === 1 ? "year" : "years"}
          </span>
          <button
            onClick={() => setBinYears((y) => Math.min(MAX_BIN_YEARS, y + 1))}
            disabled={binYears >= MAX_BIN_YEARS}
            title="Wider intervals, so fewer columns"
          >
            +
          </button>
        </div>
        <span className="tl-count">
          {grid.columns.length} column{grid.columns.length === 1 ? "" : "s"}
          {emptyColumns > 0 && ` (${emptyColumns} empty)`} ·{" "}
          {dated.length} paper{dated.length === 1 ? "" : "s"}
          {papers.length !== dated.length && ` · ${papers.length - dated.length} undated`}
        </span>
        <span className="tl-hint">
          {focusMode
            ? `${linkedCount} paper${linkedCount === 1 ? "" : "s"} highlighted${
                selectedId != null ? " · pinned, click again to unpin" : ""
              }`
            : "hover for citations · click to pin · middle click to read · scroll to zoom · drag to pan"}
        </span>
        {activeId != null && (
          <span className="tl-edge-legend">
            <b className="is-ref">
              <i /> references
            </b>
            <b className="is-cite">
              <i /> cited by
            </b>
          </span>
        )}
      </div>

      <div
        ref={hostRef}
        className={`tl-canvas${dragging ? " is-dragging" : ""}${focusMode ? " is-focused" : ""}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => onHover?.(null)}
      >
        {/* Beneath the cards: a centre-to-centre line would otherwise be drawn
            across the two cards it joins. */}
        <svg className="tl-edge-layer" width={size.w} height={size.h}>
          {edges.map((e) => (
            <g key={e.id}>
              <path className="tl-edge-halo" d={e.d} />
              <path className={`tl-edge-live is-${e.dir}`} d={e.d} />
            </g>
          ))}
        </svg>

        <div
          className="tl-bins"
          style={{ fontSize: `${Math.max(7, Math.min(12, text.metaFont * 1.1))}px` }}
        >
          {grid.columns.map((c) => (
            <span
              key={c.start}
              className={`tl-bin${c.empty ? " is-empty" : ""}`}
              style={{
                transform: `translate(${c.x + offsetX}px, ${offsetY}px)`,
                width: c.w,
              }}
            >
              {c.label}
            </span>
          ))}
        </div>

        <div
          className="tl-boxes"
          style={
            {
              "--title-font": `${text.titleFont}px`,
              "--meta-font": `${text.metaFont}px`,
              "--title-lines": text.titleLines,
            } as React.CSSProperties
          }
        >
          {grid.boxes.map(({ paper, x, y, w, h }) => {
            const isActive = paper.id === activeId;
            const isLinked = connectedIds?.has(paper.id) ?? false;
            const dimmed = focusMode && !isActive && !isLinked;
            return (
              <button
                key={paper.id}
                type="button"
                className={[
                  "tl-box",
                  KIND_CLASS[paper.kind ?? ""] ?? "kind-topic",
                  isActive ? "is-active" : "",
                  isLinked ? "is-linked" : "",
                  dimmed ? "is-dimmed" : "",
                  paper.id === selectedId ? "is-pinned" : "",
                  `status-${paper.status ?? "unread"}`,
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={{
                  transform: `translate(${x + offsetX}px, ${y + offsetY}px)`,
                  width: w,
                  height: h,
                }}
                onPointerEnter={() => onHover?.(paper.id)}
                onPointerLeave={() => onHover?.(null)}
                // Not stopped: the canvas needs this to start a pan, so the
                // grid can be dragged from anywhere. endDrag decides whether
                // it was a click on this card or a pan across it.
                onPointerDown={(e) => {
                  if (e.button === 0) pressedOn.current = paper.id;
                }}
                // Middle click opens the reader, matching how a browser opens
                // a link in a new tab. preventDefault stops autoscroll.
                onAuxClick={(e) => {
                  if (e.button !== 1) return;
                  e.preventDefault();
                  e.stopPropagation();
                  onOpenReader?.(paper.id);
                }}
                onMouseDown={(e) => e.button === 1 && e.preventDefault()}
                title={`${paper.title}\n${paper.authors.join(", ")}\n${paper.venue ?? ""} ${paper.year}\n${statusLabel(paper.status)}${paper.has_pdf ? " · PDF stored" : ""}`}
              >
                <span className="tl-box-title">{paper.title}</span>
                <span className="tl-box-meta">
                  {/* The year is its own element so it can never be the part
                      that gets truncated. On a narrow card the author name
                      gives way instead — the date is what orders the view. */}
                  <span className="tl-box-year">{paper.year}</span>
                  <span className="tl-box-who">
                    {paper.authors[0]?.split(" ").slice(-1)[0] ?? "?"}
                    {paper.authors.length > 1 ? " et al." : ""}
                  </span>
                  {!!paper.has_pdf && (
                    <span className="tl-pdf" title="PDF stored locally">
                      PDF
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>

        {!dated.length && (
          <div className="tl-empty">
            No dated papers yet. Add one from the Add tab and it will appear here.
          </div>
        )}
      </div>
    </div>
  );
}
