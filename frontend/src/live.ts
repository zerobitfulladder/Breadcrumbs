/**
 * Change notifications, within a tab and across them.
 *
 * The reader opens in its own tab, so a write made from the assistant in one
 * tab has to reach the others. BroadcastChannel does that with no server
 * involvement; where it is unavailable the local listeners still fire, so a
 * single tab always stays correct.
 */

export type ChangeKind =
  | "paper"
  | "author"
  | "link"
  | "note"
  | "status"
  | "library";

export interface ChangeEvent {
  kind: ChangeKind;
  /** Which record changed, when the writer knows. */
  id?: number;
  /** What caused it, for debugging. */
  source?: string;
}

const CHANNEL = "breadcrumbs.changes";

const listeners = new Set<(e: ChangeEvent) => void>();

let channel: BroadcastChannel | null = null;
try {
  channel = new BroadcastChannel(CHANNEL);
  channel.onmessage = (e) => {
    for (const fn of listeners) fn(e.data as ChangeEvent);
  };
} catch {
  channel = null; // older browser, or a context that forbids it
}

/** Announce a change to this tab and every other one. */
export function notifyChange(event: ChangeEvent): void {
  for (const fn of listeners) fn(event);
  try {
    channel?.postMessage(event);
  } catch {
    /* a failed broadcast must not break the write that caused it */
  }
}

/** Subscribe. Returns an unsubscribe function. */
export function onChange(fn: (e: ChangeEvent) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Which kind of change a given assistant tool represents. */
export function kindForTool(tool: string): ChangeKind {
  switch (tool) {
    case "set_author_homepage":
    case "find_wikipedia":
      return "author";
    case "add_note":
      return "note";
    case "set_paper_status":
      return "status";
    case "create_link":
      return "link";
    default:
      return "library";
  }
}
