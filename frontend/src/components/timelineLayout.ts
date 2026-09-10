/**
 * Layout for the paper grid.
 *
 * Papers are laid out in fixed columns, one per interval of `binYears`, with
 * everything published in an interval stacked in its column. Intervals with no
 * papers still get a column, so the spacing across the width is real elapsed
 * time and a long silence reads as a gap rather than closing up. A card's place
 * is decided entirely by its year — not by the viewport, the column count, or
 * how many papers precede it — so nothing moves when a panel opens or a paper
 * is selected.
 *
 * That is the whole reason for binning. Placing papers on a proportional time
 * axis cannot show a library at once: a card has a minimum readable width, so
 * once the span exceeds the canvas measured in card widths, neighbouring years
 * draw on top of each other and no amount of zooming escapes it. Binning gives
 * up resolution instead of position.
 */
import type { TimelinePaper } from "../api";

/** Exported so the caller can invert the layout, e.g. to anchor a zoom. */
export const GAP = 6;
export const PAD = 10;
/** Room above the cards for the interval labels. */
export const HEADER_H = 18;

/**
 * The smallest card the fit is allowed to produce. Below this a card holds
 * nothing legible, so a crowded library overflows and pans instead of shrinking
 * away. Zooming out deliberately may still go under it, down to ABS_MIN_W.
 */
const MIN_W = 76;
const ABS_MIN_W = 24;

/**
 * Card shape, held constant at every zoom and interval width.
 *
 * Solving the two axes independently — width from the column count, height
 * from the tallest column — let the ratio swing from 76x115 at one-year
 * intervals to 340x30 at fifty-year ones. Cards that change shape as you widen
 * the bins read as a different object each time, and the text has to be
 * re-fitted for each. One ratio keeps the grid a grid.
 */
export const CARD_ASPECT = 16 / 9;

export const DEFAULT_BIN_YEARS = 5;
export const MIN_BIN_YEARS = 1;
export const MAX_BIN_YEARS = 50;

export interface GridBox {
  paper: TimelinePaper;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface GridColumn {
  /** First year of the interval. */
  start: number;
  label: string;
  x: number;
  w: number;
  /** No papers in this interval, but it still holds its place. */
  empty: boolean;
}

export interface Grid {
  boxes: GridBox[];
  /** One per interval, left to right, empty ones included. */
  columns: GridColumn[];
  /** Column and row counts, so the card size can be recomputed cheaply. */
  cols: number;
  rows: number;
  /** True when the layout is larger than the space it was given. */
  scrolls: boolean;
}

/**
 * The card size for a given zoom, without laying anything out.
 *
 * Separated because anchoring a zoom needs the size at two different zooms and
 * must not pay for a full layout at wheel frequency.
 */
export function cardSize(
  cols: number,
  rows: number,
  width: number,
  height: number,
  zoom: number,
): { w: number; h: number } {
  const availW = Math.max(MIN_W, width - PAD * 2);
  const availH = Math.max(MIN_W / CARD_ASPECT, height - PAD * 2 - HEADER_H);

  // What each axis alone would allow, both expressed as a width so the tighter
  // of the two can win without changing the card's shape.
  const byWidth = (availW - GAP * (cols - 1)) / cols;
  const byHeight = ((availH - GAP * (rows - 1)) / rows) * CARD_ASPECT;

  // The floor belongs to the fit, not to the result: applying it afterwards
  // let it swallow the zoom entirely, so a library with many columns stayed at
  // the minimum size however far you zoomed in.
  const base = Math.max(MIN_W, Math.min(byWidth, byHeight));
  const w = Math.max(ABS_MIN_W, Math.floor(base * zoom));
  return { w, h: Math.round(w / CARD_ASPECT) };
}

/** Fractional year, so papers with a month sort inside their year. */
export function timeOf(p: TimelinePaper): number {
  return (p.year ?? 0) + (p.month ? (p.month - 1) / 12 : 0);
}

export function binOf(year: number, binYears: number): number {
  return Math.floor(year / binYears) * binYears;
}

function labelFor(start: number, binYears: number): string {
  if (binYears === 1) return String(start);
  const end = start + binYears - 1;
  // "1980–84" rather than "1980–1984": the century is already obvious from the
  // column beside it, and the shorter label fits a narrow column.
  return `${start}–${String(end).slice(-2)}`;
}

/**
 * Place every paper, or report that it will not fit.
 *
 * `zoom` scales both axes together. At 1 the cards are as large as the given
 * area allows; above it they overflow and the caller pans.
 */
export function binnedLayout(
  papers: TimelinePaper[],
  width: number,
  height: number,
  binYears: number = DEFAULT_BIN_YEARS,
  zoom: number = 1,
): Grid {
  const span = Math.max(MIN_BIN_YEARS, Math.min(MAX_BIN_YEARS, Math.round(binYears)));

  const byBin = new Map<number, TimelinePaper[]>();
  for (const paper of papers) {
    if (paper.year == null) continue;
    const bin = binOf(paper.year, span);
    const group = byBin.get(bin);
    if (group) group.push(paper);
    else byBin.set(bin, [paper]);
  }

  // Every interval between the first and the last, empty ones included, so the
  // width measures time rather than just ordering the papers. A library with a
  // century of silence in it will be mostly empty columns; that is the point.
  const occupied = [...byBin.keys()].sort((a, b) => a - b);
  const starts: number[] = [];
  if (occupied.length) {
    for (let y = occupied[0]; y <= occupied[occupied.length - 1]; y += span) {
      starts.push(y);
    }
  }
  const cols = Math.max(1, starts.length);
  const rows = Math.max(1, ...[...byBin.values()].map((g) => g.length));

  const { w, h } = cardSize(cols, rows, width, height, zoom);

  const columns: GridColumn[] = [];
  const boxes: GridBox[] = [];
  starts.forEach((start, col) => {
    const x = PAD + col * (w + GAP);
    const group = byBin.get(start);
    columns.push({ start, label: labelFor(start, span), x, w, empty: !group });
    if (!group) return;
    group
      // Newest at the top. Within five years the ordering is a detail rather
      // than the story, and the recent end of a column is the part usually
      // being looked for.
      .sort((a, b) => timeOf(b) - timeOf(a) || a.title.localeCompare(b.title))
      .forEach((paper, row) => {
        boxes.push({ paper, x, y: PAD + HEADER_H + row * (h + GAP), w, h });
      });
  });

  return {
    boxes,
    columns,
    cols,
    rows,
    scrolls:
      cols * (w + GAP) - GAP > width - PAD * 2 ||
      rows * (h + GAP) - GAP > height - PAD * 2 - HEADER_H,
  };
}

export interface CardText {
  titleFont: number;
  metaFont: number;
  /** How many lines of title actually fit above the meta row. */
  titleLines: number;
}

const clamp = (lo: number, v: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * Type sized to the card rather than fixed.
 *
 * Cards resize with the zoom and the interval width, so a fixed font either
 * spills out of a small card — clipped mid-word, which is what "the name does
 * not fit" looks like — or leaves a large one mostly empty. Both dimensions
 * constrain it: height decides how many lines there is room for, width decides
 * how much of a line is readable, and the smaller of the two wins.
 *
 * The line count is then derived from what is actually left after the meta row,
 * so the title is clamped to lines that fit instead of overflowing hidden.
 */
export function cardText(w: number, h: number): CardText {
  const PAD_V = 8;
  // The upper bounds exist so a lone paper in a wide column does not get
  // poster-sized type, not to hold the text at a reading size — set near the
  // old defaults they capped out the moment a card grew, which is what made a
  // zoomed-in card read as a big box with small print in it. They now sit far
  // enough out that the proportional rule, not the cap, is what is in force
  // across the whole usable zoom range.
  const metaFont = clamp(4.5, Math.min(h * 0.14, w * 0.07), 15);
  const metaH = metaFont * 1.5;
  const titleFont = clamp(5, Math.min(h * 0.17, w * 0.085), 24);
  const room = h - PAD_V - metaH;
  return {
    titleFont: Math.round(titleFont * 10) / 10,
    metaFont: Math.round(metaFont * 10) / 10,
    titleLines: Math.max(1, Math.floor(room / (titleFont * 1.2))),
  };
}
