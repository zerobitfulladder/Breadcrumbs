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

export interface Selection {
  page: number;
  text: string;
  rects: HighlightRect[];
  pageWidth: number;
  pageHeight: number;
  /** Where to anchor the popover, in client coordinates. */
  anchor: { x: number; y: number };
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
  const [scale, setScale] = useState(0.6);
  const [width, setWidth] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Our own painting of the live selection, per page. */
  const [selBands, setSelBands] = useState<{ page: number; rects: HighlightRect[] } | null>(
    null,
  );
  const hostRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageSizes = useRef<Map<number, { width: number; height: number }>>(new Map());

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
  const readSelection = useCallback((event?: PointerEvent) => {
    // Clicking a button in the selection menu collapses the browser selection,
    // which would otherwise be read here as "nothing selected" and wipe the
    // menu the moment it was used. The rectangles are already captured, so
    // anything inside the menu is simply ignored.
    const target = event?.target as Element | null;
    if (target?.closest?.("[data-keep-selection]")) return;

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
    });
  }, [onSelect]);

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
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;   // plain scrolling is untouched
      e.preventDefault();
      // ~3% per wheel notch. The old exponent moved 40% a click, which
      // overshot every time.
      setScale((prev) =>
        Math.min(3, Math.max(0.5, prev * Math.pow(0.9997, e.deltaY))),
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onUp = (e: PointerEvent) => window.setTimeout(() => readSelection(e), 0);
    document.addEventListener("pointerup", onUp);
    return () => document.removeEventListener("pointerup", onUp);
  }, [readSelection]);

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
  const onPageLoad = useCallback((page: { view: number[]; pageNumber: number }) => {
    const view = page.view;
    pageSizes.current.set(page.pageNumber, {
      width: view[2] - view[0],
      height: view[3] - view[1],
    });
  }, []);

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
        <button onClick={() => setScale((v) => Math.max(0.5, v - 0.1))}>−</button>
        <span className="pv-zoom">{Math.round(scale * 100)}%</span>
        <button onClick={() => setScale((v) => Math.min(3, v + 0.1))}>+</button>
        <span className="pv-pages">
          {pageCount ? `${pageCount} page${pageCount === 1 ? "" : "s"}` : ""}
        </span>
        <span className="pv-hint">
          select text to highlight · ctrl-scroll to zoom
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
          <Document
            file={file}
            onLoadSuccess={onDocumentLoad}
            onLoadError={onDocumentError}
            loading={LOADING}
          >
            {pageNumbers.map((number) => (
              <div className="pv-page" key={number} data-page={number}>
                <Page
                  pageNumber={number}
                  width={width * scale}
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
                        title={h.comment || h.quoted || undefined}
                        onClick={() => onHighlightClick?.(h.id)}
                      />
                    )),
                  )}
                </div>
              </div>
            ))}
          </Document>
        )}
      </div>
    </div>
  );
}
