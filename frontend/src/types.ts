export type PaperStatus = "unread" | "queued" | "skimmed" | "read" | "archived";

export type ShelfKind =
  | "topic"
  | "supplement"
  | "dataset"
  | "background"
  | "method";

export interface Paper {
  id: number;
  title: string;
  /** Publication year. Papers without a year are not placed on the timeline. */
  year: number | null;
  /** 1-12, optional. Used for sub-year placement when present. */
  month?: number | null;
  authors: string[];
  venue?: string | null;
  status?: PaperStatus;
  /** Drives the box colour. */
  kind?: ShelfKind;
}
