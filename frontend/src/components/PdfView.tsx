import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { Highlight, HighlightRect } from "../api";
import "react-pdf/dist/Page/TextLayer.css";
import "./PdfView.css";

// pdf.js runs its parser in a worker. Bundled from the installed copy rather
// than a CDN, so the reader keeps working offline.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/**
 * Collapse a range's client rectangles into one per line.
 *
 * pdf.js splits every line into several spans, so a selection returns a
 * rectangle per span. They overlap, and because marks are drawn with multiply
 * blending each overlap darkens twice — which is why the middle of a
 * multi-line highlight looked painted over twice while its ends did not.
 * Merging by line also trims the line box down to the glyphs.
 */
function mergeByLine(rects: DOMRect[], box: DOMRect): HighlightRect[] {
  if (!rects.length) return [];

  // Rows are decided by vertical centre, tolerating the small differences
  // between spans of different font sizes on one line.
  const rows: DOMRect[][] = [];
  for (const r of [...rects].sort((a, b) => a.top - b.top || a.left - b.left)) {
    const centre = r.top + r.height / 2;
    const row = rows.find((group) => {
      const first = group[0];
      return Math.abs(centre - (first.top + first.height / 2)) < first.height * 0.6;
    });
    if (row) row.push(r);
    else rows.push([r]);
  }

  // A client rectangle covers the whole line box, which is taller than the
  // glyphs; trimming lands the mark on the text rather than the leading.
  const bands = rows.map((row) => {
    const top = Math.min(...row.map((r) => r.top));
    const bottom = Math.max(...row.map((r) => r.bottom));
    const trim = (bottom - top) * 0.12;
    return {
      left: Math.min(...row.map((r) => r.left)),
      right: Math.max(...row.map((r) => r.right)),
      top: top + trim,
      bottom: bottom - trim,
    };
  });

  // Close the gap between consecutive lines of the same passage. Trimming each
  // line independently left a stripe of unmarked paper between them, which a
  // real highlighter would never leave. Only small gaps are closed, so two
  // separate paragraphs do not weld together.
  for (let i = 1; i < bands.length; i += 1) {
    const previous = bands[i - 1];
    const current = bands[i];
    const gap = current.top - previous.bottom;
    if (gap > 0 && gap < (current.bottom - current.top) * 0.9) {
      const midpoint = previous.bottom + gap / 2;
      previous.bottom = midpoint;
      current.top = midpoint;
    }
  }

  return bands.map((b) => ({
    x: (b.left - box.left) / box.width,
    y: (b.top - box.top) / box.height,
    w: (b.right - b.left) / box.width,
    h: (b.bottom - b.top) / box.height,
  }));
}

/** Stable node: an inline element would remount the Document each render. */
const LOADING = <div className="pv-loading">Loading the document…</div>;

/**
 * Resolutions a page is ever drawn at. Coarse on purpose — each change costs a
 * repaint of every page — with CSS covering everything in between.
 */
const RENDER_STEPS = [0.75, 1, 1.5, 2.25, 3];

export interface Selection {
  page: number;
  text: string;
  rects: HighlightRect[];
  pageWidth: number;
  pageHeight: number;
  /** Where to anchor the popover, in client coordinates. */
  anchor: { x: number; y: number };
  /**
   * "text" came from selecting words; "region" from dragging a box over a
   * scanned page, and carries no text. The popover uses this to drop the
   * choices that need words to work on.
   */
  kind: "text" | "region";
}

interface Props {
  file: string;
  highlights: Highlight[];
  activeHighlightId?: number | null;
  onSelect: (selection: Selection | null) => void;
  onHighlightClick?: (id: number) => void;
  /** The page most in view, so the assistant can be handed its text. */
  onVisiblePage?: (page: number) => void;
  /**
   * Scroll here. `flash` paints a brief marker over it, which suits the
   * assistant pointing something out but not jumping to a highlight that is
   * already drawn.
   */
  reveal?: {
    page: number;
    rects: HighlightRect[];
    token: number;
    flash?: boolean;
  } | null;
}

/**
 * The PDF, with its text layer live.
 *
 * Built on pdf.js rather than the browser's own viewer: an `<object>` tag
 * gives no access to the text, so selecting a passage, anchoring a note to it
 * or drawing a mark over it are all impossible. Here every page renders a text
 * layer we can read selections from and position overlays against.
 *
 * Highlight rectangles are stored as fractions of the page, so they land
 * correctly at any zoom and any render width.
 */
export default function PdfView({
  file,
  highlights,
  activeHighlightId,
  onSelect,
  onHighlightClick,
  onVisiblePage,
  reveal = null,
}: Props) {
  const [pageCount, setPageCount] = useState(0);
  // 60%: an academic page at full width is wider than most reading panes, and
  // starting zoomed out shows the whole column.
  /**
   * The resolution the canvases are drawn at — deliberately coarser than the
   * zoom, and changed as rarely as possible.
   *
   * Changing it is the flicker. react-pdf redraws a page by resizing its
   * canvas, and resizing a canvas clears it, so there is a transparent gap
   * until the render task repaints. Tying that to the zoom meant a flash at
   * the end of every gesture.
   *
   * Snapping to a ladder means most zooming needs no redraw at all, and the
   * value only ever rises: coming back down reuses the sharper canvas, so the
   * way out of a zoom is always free. Drawn larger than shown, CSS scales it
   * down, which also looks better than drawing at exactly the display size.
   */
  const [renderScale, setRenderScale] = useState(RENDER_STEPS[0]);
  /**
   * The zoom the gesture is at, which is not always the one rendered.
   *
   * Re-rendering a PDF page is expensive, and doing it on every wheel tick
   * tore the canvas down and put a placeholder up dozens of times per gesture —
   * the flicker to blank. So the wheel moves `live`, the pages are scaled by
   * CSS in the meantime, and the canvas is re-rendered once the gesture stops.
   *
   * `zoom` rather than `transform`: zoom affects layout, so the scroll extents
   * grow with the preview and scrolling keeps working mid-gesture.
   */
  const [live, setLive] = useState(0.6);
  const [width, setWidth] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Our own painting of the live selection, per page. */
  const [selBands, setSelBands] = useState<{ page: number; rects: HighlightRect[] } | null>(
    null,
  );
  const hostRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageSizes = useRef<Map<number, { width: number; height: number }>>(new Map());

  /** Draw a box instead of selecting words. Needed for scanned pages, which
   *  have no text to select, and useful on any page for marking a figure. */
  const [regionMode, setRegionMode] = useState(false);
  /** Set once a page reports it has no text of its own. */
  const [looksScanned, setLooksScanned] = useState(false);
  const scannedPages = useRef<Set<number>>(new Set());
  /** The box being dragged, in page fractions. */
  const [drawing, setDrawing] = useState<{ page: number; rect: HighlightRect } | null>(null);
  /**
   * The note shown while the pointer rests on a mark that carries one.
   *
   * Held as the mark it belongs to — page plus rectangle in page fractions —
   * rather than as screen coordinates. Pixels captured on hover go stale the
   * moment anything moves, which left the bubble hanging in place while the
   * page scrolled out from under it.
   */
  const [hoveredNote, setHoveredNote] = useState<{
    text: string;
    page: number;
    rect: HighlightRect;
  } | null>(null);
  /** Where that mark currently is on screen; recomputed as the page moves. */
  const [notePos, setNotePos] = useState<{ x: number; top: number; bottom: number } | null>(null);
  const hoverOut = useRef<number | null>(null);
  const dragStart = useRef<{ page: number; x: number; y: number; box: DOMRect } | null>(null);

  useLayoutEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const next = Math.max(320, entry.contentRect.width - 48);
      // Ignore the pixel or two a scrollbar appearing takes away. Re-rendering
      // every page for that caused a visible flash on every scroll.
      setWidth((prev) => (prev !== null && Math.abs(prev - next) < 8 ? prev : next));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /**
   * Turn the live DOM selection into page-relative rectangles.
   *
   * Client rectangles are absolute pixels at the current zoom, so each is
   * divided by the rendered page box. That makes the stored geometry
   * independent of how wide the reader happens to be.
   */
  const readSelection = useCallback(() => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      onSelect(null);
      return;
    }
    const text = selection.toString().trim();
    if (!text) {
      onSelect(null);
      return;
    }

    const range = selection.getRangeAt(0);
    const pageEl = (range.startContainer.parentElement as HTMLElement | null)?.closest(
      ".pv-page",
    ) as HTMLElement | null;
    if (!pageEl) {
      onSelect(null);
      return;
    }

    const pageNumber = Number(pageEl.dataset.page);
    const box = pageEl.getBoundingClientRect();
    const raw = Array.from(range.getClientRects()).filter((r) => r.width >= 1 && r.height >= 1);
    const rects = mergeByLine(raw, box);
    if (!rects.length) {
      onSelect(null);
      return;
    }

    const last = range.getClientRects()[range.getClientRects().length - 1];
    const size = pageSizes.current.get(pageNumber);
    onSelect({
      page: pageNumber,
      text,
      rects,
      pageWidth: size?.width ?? box.width,
      pageHeight: size?.height ?? box.height,
      anchor: { x: last.left + last.width / 2, y: last.bottom },
      kind: "text",
    });
  }, [onSelect]);

  /**
   * Drag a box over a page and turn it into a highlight.
   *
   * A scanned PDF is an image: there are no glyphs to select, so the text path
   * has nothing to work with and a note cannot be anchored to anything. The
   * geometry is the same either way — rectangles as fractions of the page — so
   * a drawn box stores and paints exactly like a selected passage, and carries
   * no quoted text.
   */
  const onRegionPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!regionMode || e.button !== 0) return;
      const pageEl = (e.target as HTMLElement).closest(".pv-page") as HTMLElement | null;
      if (!pageEl) return;
      e.preventDefault();
      const box = pageEl.getBoundingClientRect();
      dragStart.current = {
        page: Number(pageEl.dataset.page),
        x: (e.clientX - box.left) / box.width,
        y: (e.clientY - box.top) / box.height,
        box,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      onSelect(null);
    },
    [regionMode, onSelect],
  );

  const onRegionPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    if (!start) return;
    const x = (e.clientX - start.box.left) / start.box.width;
    const y = (e.clientY - start.box.top) / start.box.height;
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    setDrawing({
      page: start.page,
      rect: {
        x: clamp(Math.min(start.x, x)),
        y: clamp(Math.min(start.y, y)),
        w: Math.abs(clamp(x) - clamp(start.x)),
        h: Math.abs(clamp(y) - clamp(start.y)),
      },
    });
  }, []);

  const onRegionPointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const start = dragStart.current;
      dragStart.current = null;
      if (!start || !drawing) {
        setDrawing(null);
        return;
      }
      const { page, rect } = drawing;
      setDrawing(null);
      // A click, or a slip of the hand, is not a region. Below this it is
      // almost certainly a misfire, and an invisible highlight is worse than
      // none: it cannot be seen to be removed.
      if (rect.w < 0.01 || rect.h < 0.005) return;
      const size = pageSizes.current.get(page);
      onSelect({
        page,
        text: "",
        rects: [rect],
        pageWidth: size?.width ?? start.box.width,
        pageHeight: size?.height ?? start.box.height,
        anchor: { x: e.clientX, y: e.clientY },
        kind: "region",
      });
    },
    [drawing, onSelect],
  );

  /**
   * Show a highlight's note under the pointer.
   *
   * A note you cannot see without clicking is a note you forget you wrote. The
   * native `title` tooltip technically showed it, but only after about a
   * second and with no way to style or place it.
   *
   * Coordinates are read from the mark and kept in client space: the pages sit
   * inside a CSS `zoom` wrapper and a scroll container, either of which would
   * otherwise scale or clip the bubble.
   */
  /** Take the bubble down now. */
  const hideNoteNow = useCallback(() => {
    if (hoverOut.current) {
      window.clearTimeout(hoverOut.current);
      hoverOut.current = null;
    }
    setHoveredNote(null);
  }, []);

  /**
   * Take it down shortly, so crossing the gap between two rectangles of one
   * passage does not blink it off and straight back on.
   *
   * A pending hide is left alone rather than restarted. Restarting it meant
   * every pointermove pushed the deadline back, so the bubble outlived the
   * hover for as long as the pointer kept moving and only vanished once it
   * came to rest.
   */
  const hideNote = useCallback(() => {
    if (hoverOut.current) return;
    hoverOut.current = window.setTimeout(() => {
      hoverOut.current = null;
      setHoveredNote(null);
    }, 90);
  }, []);

  const trackNote = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const pageEl = (e.currentTarget as HTMLElement).closest(".pv-page") as HTMLElement | null;
      if (!pageEl) return;
      const page = Number(pageEl.dataset.page);
      const box = pageEl.getBoundingClientRect();
      const px = (e.clientX - box.left) / box.width;
      const py = (e.clientY - box.top) / box.height;

      // Last drawn wins, matching what the eye sees where marks overlap.
      let found: { text: string; r: HighlightRect } | null = null;
      for (const h of highlights) {
        if (h.page !== page || !h.comment) continue;
        for (const r of h.rects) {
          if (px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h) {
            found = { text: h.comment, r };
          }
        }
      }

      if (!found) {
        if (hoveredNote) hideNote();
        return;
      }
      if (hoverOut.current) {
        window.clearTimeout(hoverOut.current);
        hoverOut.current = null;
      }
      // Steady while the pointer stays inside the same mark: re-anchoring on
      // every move would make the bubble crawl around under the cursor.
      if (hoveredNote?.text === found.text) return;
      setHoveredNote({ text: found.text, page, rect: found.r });
    },
    [highlights, hoveredNote, hideNote],
  );

  useEffect(() => () => {
    if (hoverOut.current) window.clearTimeout(hoverOut.current);
  }, []);

  // Follow the mark while the page moves under it. The bubble is positioned
  // fixed — it has to be, or the zoom wrapper would scale it and the scroll
  // container would clip it — so its coordinates are recomputed rather than
  // inherited from a scrolling ancestor.
  useLayoutEffect(() => {
    // Nothing to clear when there is no note: the bubble is only rendered when
    // both the note and a position exist, and this runs before paint, so a
    // position left over from the previous mark is never shown.
    if (!hoveredNote) return;
    const scroller = scrollRef.current;
    const place = () => {
      const pageEl = scroller?.querySelector<HTMLElement>(
        `.pv-page[data-page="${hoveredNote.page}"]`,
      );
      if (!pageEl) return;
      const box = pageEl.getBoundingClientRect();
      const r = hoveredNote.rect;
      setNotePos({
        x: box.left + (r.x + r.w / 2) * box.width,
        top: box.top + r.y * box.height,
        bottom: box.top + (r.y + r.h) * box.height,
      });
    };
    place();
    scroller?.addEventListener("scroll", place, { passive: true });
    window.addEventListener("resize", place);
    return () => {
      scroller?.removeEventListener("scroll", place);
      window.removeEventListener("resize", place);
    };
  }, [hoveredNote]);

  // Scroll to whatever the assistant is pointing at. Centred rather than
  // scrolled-to-top: a passage in the middle of a page is easier to find when
  // it is in the middle of the view.
  useEffect(() => {
    if (!reveal || !scrollRef.current) return;
    const pageEl = scrollRef.current.querySelector<HTMLElement>(
      `.pv-page[data-page="${reveal.page}"]`,
    );
    if (!pageEl) return;

    const target = Array.isArray(reveal.rects) ? reveal.rects[0] : undefined;
    const offsetInPage = target ? target.y * pageEl.offsetHeight : 0;
    const top =
      pageEl.offsetTop + offsetInPage - scrollRef.current.clientHeight / 2 + 40;
    scrollRef.current.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }, [reveal]);

  // The browser paints its own selection as a flat box over the full em box of
  // each span: it washes the glyphs out and spills into the line below. Ours is
  // hidden in CSS and redrawn here from the same per-line bands the highlights
  // use, so selecting looks like what you are about to get.
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        setSelBands(null);
        return;
      }
      const range = sel.getRangeAt(0);
      const pageEl = (range.startContainer.parentElement as HTMLElement | null)?.closest(
        ".pv-page",
      ) as HTMLElement | null;
      if (!pageEl) {
        setSelBands(null);
        return;
      }
      const box = pageEl.getBoundingClientRect();
      const raw = Array.from(range.getClientRects()).filter(
        (r) => r.width >= 1 && r.height >= 1,
      );
      setSelBands({ page: Number(pageEl.dataset.page), rects: mergeByLine(raw, box) });
    };

    // Coalesced to a frame: selectionchange fires continuously while dragging.
    const onChange = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    document.addEventListener("selectionchange", onChange);
    return () => {
      document.removeEventListener("selectionchange", onChange);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  // Ctrl-wheel zooms the document, not the browser window — but only while the
  // pointer is over the pages. Elsewhere it stays the browser's own zoom.
  // Registered non-passively so the default can be prevented.
  // Read inside the wheel listener, which is bound once. Written there too, so
  // two ticks in one frame compose instead of both reading the same value.
  const liveRef = useRef(live);
  liveRef.current = live;

  /** The zoom the DOM is currently laid out at, which lags `live` by a commit. */
  const appliedLive = useRef(live);
  /**
   * Where to put the scroll once the new zoom is in the DOM.
   *
   * Held in layout units at zoom 1, so it stays valid however many ticks land
   * before the correction runs.
   */
  const anchor = useRef<{ baseX: number; baseY: number; viewX: number; viewY: number } | null>(
    null,
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;   // plain scrolling is untouched
      e.preventDefault();
      // ~3% per wheel notch. The old exponent moved 40% a click, which
      // overshot every time.
      const prev = liveRef.current;
      const next = Math.min(3, Math.max(0.5, prev * Math.pow(0.9997, e.deltaY)));
      if (next === prev) return;

      liveRef.current = next;

      // Keep the point under the cursor still. Recorded in layout units at
      // zoom 1 against the zoom the DOM actually has, so it does not matter how
      // many ticks arrive before the correction is applied.
      const rect = el.getBoundingClientRect();
      const viewX = e.clientX - rect.left;
      const viewY = e.clientY - rect.top;
      const at = appliedLive.current;
      anchor.current = {
        baseX: (el.scrollLeft + viewX) / at,
        baseY: (el.scrollTop + viewY) / at,
        viewX,
        viewY,
      };
      setLive(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  /**
   * Put the scroll back after the zoom is in the DOM.
   *
   * A layout effect, not requestAnimationFrame: rAF can run before React has
   * committed, so the scroll was written against the old, smaller layout and
   * the browser clamped it — then the content grew and the view slid. Here the
   * new size is already in place and scrollHeight is final.
   */
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const a = anchor.current;
    if (el && a) {
      el.scrollLeft = a.baseX * live - a.viewX;
      el.scrollTop = a.baseY * live - a.viewY;
      anchor.current = null;
    }
    appliedLive.current = live;
  }, [live]);

  /** The +/- buttons: one deliberate step, rendered at once. */
  const step = useCallback((delta: number) => {
    setLive(Math.min(3, Math.max(0.5, Math.round((liveRef.current + delta) * 100) / 100)));
  }, []);

  // Raise the resolution once the gesture settles, and only if the zoom has
  // outgrown what is drawn. Debounced so sweeping across several steps in one
  // gesture costs one redraw rather than one per step.
  useEffect(() => {
    const needed = RENDER_STEPS.find((s) => s >= live) ?? RENDER_STEPS[RENDER_STEPS.length - 1];
    if (needed <= renderScale) return;
    const timer = window.setTimeout(() => setRenderScale(needed), 180);
    return () => window.clearTimeout(timer);
  }, [live, renderScale]);

  useEffect(() => {
    const onUp = (e: PointerEvent) => {
      // Clicking a button in the selection menu collapses the browser
      // selection, which would otherwise be read as "nothing selected" and
      // wipe the menu the moment it was used.
      //
      // Whether the click was in the menu has to be decided *now*. Reading the
      // selection is deferred a tick so the browser has settled, and by then
      // React has re-rendered — pressing Note swaps the menu's contents for the
      // note field, detaching the very button that was clicked. closest() on a
      // detached node finds none of its old ancestors, so the check quietly
      // failed and the note field was dismissed as it appeared.
      const inMenu = !!(e.target as Element | null)?.closest?.("[data-keep-selection]");
      window.setTimeout(() => {
        // In box mode there is no DOM selection to read — the drag handler has
        // just published one of its own. Reading here would find nothing
        // selected and clear it, closing the menu the instant it appeared.
        if (!inMenu && !regionMode) readSelection();
      }, 0);
    };
    document.addEventListener("pointerup", onUp);
    return () => document.removeEventListener("pointerup", onUp);
  }, [readSelection, regionMode]);

  // Which page is being read. Watched rather than computed from scroll offset
  // so it stays right at any zoom and with pages of differing heights.
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !pageCount || !onVisiblePage) return;
    const seen = new Map<number, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const page = Number((entry.target as HTMLElement).dataset.page);
          seen.set(page, entry.intersectionRatio);
        }
        let best = 0;
        let bestRatio = 0;
        for (const [page, ratio] of seen) {
          if (ratio > bestRatio) {
            best = page;
            bestRatio = ratio;
          }
        }
        if (best) onVisiblePage(best);
      },
      { root, threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    for (const el of root.querySelectorAll(".pv-page")) observer.observe(el);
    return () => observer.disconnect();
  }, [pageCount, onVisiblePage]);

  // react-pdf re-renders a page whenever a prop identity changes, and an inline
  // callback is a new identity every render — so any state change here made
  // every canvas redraw, which is the flash on scroll. These are stable.
  const onPageLoad = useCallback(
    (page: {
      view: number[];
      pageNumber: number;
      getTextContent?: () => Promise<{ items: unknown[] }>;
    }) => {
      const view = page.view;
      pageSizes.current.set(page.pageNumber, {
        width: view[2] - view[0],
        height: view[3] - view[1],
      });

      // A scanned page carries no text items at all. Asking the page itself is
      // exact, where inspecting the rendered text layer would race the render.
      // Once one page comes back empty, offer the box tool rather than leaving
      // the reader looking broken: selecting there can never do anything.
      void page.getTextContent?.().then((content) => {
        if (content.items.length) return;
        scannedPages.current.add(page.pageNumber);
        setLooksScanned(true);
      }).catch(() => undefined);
    },
    [],
  );

  const onDocumentLoad = useCallback(
    ({ numPages }: { numPages: number }) => setPageCount(numPages),
    [],
  );

  const onDocumentError = useCallback(
    (e: Error) => setError(`Could not open the PDF: ${e.message}`),
    [],
  );

  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, i) => i + 1),
    [pageCount],
  );

  const byPage = new Map<number, Highlight[]>();
  for (const h of highlights) {
    const list = byPage.get(h.page) ?? [];
    list.push(h);
    byPage.set(h.page, list);
  }

  return (
    <div className="pv-root" ref={hostRef}>
      <div className="pv-toolbar">
        <button onClick={() => step(-0.1)}>−</button>
        <span className="pv-zoom">{Math.round(live * 100)}%</span>
        <button onClick={() => step(0.1)}>+</button>
        <span className="pv-pages">
          {pageCount ? `${pageCount} page${pageCount === 1 ? "" : "s"}` : ""}
        </span>
        <button
          className={`pv-region${regionMode ? " is-on" : ""}`}
          onClick={() => setRegionMode((on) => !on)}
          title="Draw a box to highlight part of the page. Needed on scanned PDFs."
          aria-pressed={regionMode}
        >
          ▢ Box
        </button>
        <span className="pv-hint">
          {regionMode
            ? "drag a box to highlight · ctrl-scroll to zoom"
            : looksScanned
              ? "this PDF is scanned — use Box to highlight · ctrl-scroll to zoom"
              : "select text to highlight · ctrl-scroll to zoom"}
        </span>
      </div>

      <div className="pv-scroll" ref={scrollRef}>
        {error ? (
          <div className="pv-error">{error}</div>
        ) : width === null ? (
          // Wait for the real width. Rendering at a guess and then re-rendering
          // is what made the document appear zoomed out for a moment.
          <div className="pv-loading">Measuring…</div>
        ) : (
          // Scaled by CSS while a gesture is in flight, exactly 1 the rest of
          // the time, so nothing is distorted once the canvas has caught up.
          <div className="pv-zoomer" style={{ zoom: live / renderScale }}>
          <Document
            file={file}
            onLoadSuccess={onDocumentLoad}
            onLoadError={onDocumentError}
            loading={LOADING}
          >
            {pageNumbers.map((number) => (
              <div
                className={`pv-page${regionMode ? " is-drawing" : ""}`}
                key={number}
                data-page={number}
                onPointerDown={onRegionPointerDown}
                onPointerMove={(e) => {
                  onRegionPointerMove(e);
                  if (!dragStart.current) trackNote(e);
                }}
                onPointerUp={onRegionPointerUp}
                onPointerLeave={hideNoteNow}
              >
                <Page
                  pageNumber={number}
                  width={width * renderScale}
                  renderAnnotationLayer={false}
                  renderTextLayer
                  // Native page size in points is recorded here, so a stored
                  // highlight can be converted back to absolute units.
                  onLoadSuccess={onPageLoad}
                />
                <div className="pv-marks">
                  {reveal?.page === number &&
                    reveal.flash !== false &&
                    Array.isArray(reveal.rects) &&
                    reveal.rects.map((r, i) => (
                      <span
                        key={`reveal-${reveal.token}-${i}`}
                        className="pv-reveal"
                        style={{
                          left: `${r.x * 100}%`,
                          top: `${r.y * 100}%`,
                          width: `${r.w * 100}%`,
                          height: `${r.h * 100}%`,
                        }}
                      />
                    ))}
                  {drawing?.page === number && (
                    <span
                      className="pv-drawing"
                      style={{
                        left: `${drawing.rect.x * 100}%`,
                        top: `${drawing.rect.y * 100}%`,
                        width: `${drawing.rect.w * 100}%`,
                        height: `${drawing.rect.h * 100}%`,
                      }}
                    />
                  )}
                  {selBands?.page === number &&
                    selBands.rects.map((r, i) => (
                      <span
                        key={`sel-${i}`}
                        className="pv-sel"
                        style={{
                          left: `${r.x * 100}%`,
                          top: `${r.y * 100}%`,
                          width: `${r.w * 100}%`,
                          height: `${r.h * 100}%`,
                        }}
                      />
                    ))}
                  {(byPage.get(number) ?? []).map((h) =>
                    h.rects.map((r, i) => (
                      <button
                        key={`${h.id}-${i}`}
                        className={`pv-mark${h.id === activeHighlightId ? " is-active" : ""}${
                          h.comment ? " has-note" : ""
                        }`}
                        style={{
                          left: `${r.x * 100}%`,
                          top: `${r.y * 100}%`,
                          width: `${r.w * 100}%`,
                          height: `${r.h * 100}%`,
                          background: h.color ?? "#fde047",
                        }}
                        // Only as a fallback: a note gets the bubble below,
                        // and two tooltips for one mark is one too many.
                        title={h.comment ? undefined : h.quoted || undefined}
                        onClick={() => onHighlightClick?.(h.id)}
                      />
                    )),
                  )}
                </div>
              </div>
            ))}
          </Document>
          </div>
        )}
      </div>

      {hoveredNote && notePos && (
        <div
          className="pv-note"
          role="tooltip"
          style={
            // Above the mark when there is room, below it otherwise, so a
            // highlight near the top of the window still shows its note.
            notePos.top > 96
              ? { left: notePos.x, top: notePos.top - 8, transform: "translate(-50%, -100%)" }
              : { left: notePos.x, top: notePos.bottom + 8, transform: "translate(-50%, 0)" }
          }
        >
          {hoveredNote.text}
        </div>
      )}
    </div>
  );
}
