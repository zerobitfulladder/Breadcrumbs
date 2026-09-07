/**
 * Reading statuses, in the order they progress.
 *
 * "reading" sits between queued and skimmed: started and not finished, which is
 * the one you need to find again tomorrow.
 */
export const STATUSES = ["unread", "queued", "reading", "skimmed", "read", "archived"] as const;

export type Status = (typeof STATUSES)[number];

/** Title-cased for display. Stored values stay lowercase. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Unread";
  return status.charAt(0).toUpperCase() + status.slice(1);
}
