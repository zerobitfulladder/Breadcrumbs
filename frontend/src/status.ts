/** Reading statuses, in the order they progress. */
export const STATUSES = ["unread", "queued", "skimmed", "read", "archived"] as const;

export type Status = (typeof STATUSES)[number];

/** Title-cased for display. Stored values stay lowercase. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Unread";
  return status.charAt(0).toUpperCase() + status.slice(1);
}
