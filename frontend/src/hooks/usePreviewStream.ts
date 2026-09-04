import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, previewStream } from "../api";
import type { EdgeRecord, PaperRecord, Preview, SourcePanel, WorkVersion } from "../api";

export interface EdgeState {
  loading: boolean;
  count: number;
  note: string;
  sample: EdgeRecord[];
}

export const EMPTY_EDGES: EdgeState = { loading: false, count: 0, note: "", sample: [] };

export interface PreviewState {
  streaming: boolean;
  error: string | null;
  /** Full-text matches, when the input was not an identifier. */
  matches: Awaited<ReturnType<typeof api.search>>["results"] | null;
  order: string[];
  panels: Record<string, SourcePanel>;
  merged: PaperRecord | null;
  versions: WorkVersion[];
  /** Set when the check for other records of the same work failed. */
  versionsError: string | null;
  refs: EdgeState;
  cites: EdgeState;
  preview: Preview | null;
}

/**
 * Runs one paper lookup and exposes it as it arrives.
 *
 * Shared by the Add page and the preview card opened from an author's
 * bibliography, so both show exactly the same thing from one implementation.
 */
export function usePreviewStream() {
  const [state, setState] = useState<PreviewState>({
    streaming: false,
    error: null,
    matches: null,
    order: [],
    panels: {},
    merged: null,
    versions: [],
    versionsError: null,
    refs: EMPTY_EDGES,
    cites: EMPTY_EDGES,
    preview: null,
  });

  const closeRef = useRef<(() => void) | null>(null);
  useEffect(() => () => closeRef.current?.(), []);

  const reset = useCallback(() => {
    closeRef.current?.();
    setState({
      streaming: false,
      error: null,
      matches: null,
      order: [],
      panels: {},
      merged: null,
      versions: [],
      versionsError: null,
      refs: EMPTY_EDGES,
      cites: EMPTY_EDGES,
      preview: null,
    });
  }, []);

  const lookup = useCallback(
    (identifier: string, titleHint?: string) => {
      const query = identifier.trim();
      if (!query) return;
      closeRef.current?.();
      setState((s) => ({
        ...s,
        streaming: true,
        error: null,
        matches: null,
        order: [],
        panels: {},
        merged: null,
        versions: [],
        versionsError: null,
        refs: EMPTY_EDGES,
        cites: EMPTY_EDGES,
        preview: null,
      }));

      closeRef.current = previewStream(query, titleHint, {
        onStart: (d) =>
          setState((s) => ({
            ...s,
            order: d.source_order,
            panels: Object.fromEntries(d.sources.map((p) => [p.name, p])),
          })),
        onSource: (panel) =>
          setState((s) => ({ ...s, panels: { ...s.panels, [panel.name]: panel } })),
        onMerged: (d) => setState((s) => ({ ...s, merged: d.merged })),
        onVersions: (d) =>
          setState((s) => ({ ...s, versions: d.versions, versionsError: d.error ?? null })),
        onEdgesPending: (which) =>
          setState((s) => ({
            ...s,
            [which === "references" ? "refs" : "cites"]: {
              ...(which === "references" ? s.refs : s.cites),
              loading: true,
            },
          })),
        onEdges: (d) =>
          setState((s) => ({
            ...s,
            [d.which === "references" ? "refs" : "cites"]: {
              loading: false,
              count: d.count,
              note: d.note,
              sample: d.sample,
            },
          })),
        onDone: (p) => setState((s) => ({ ...s, preview: p, streaming: false })),
        onError: async (message) => {
          // "Not an identifier" is the cue to try a full title search instead.
          if (/could not read that as an identifier/i.test(message)) {
            try {
              const { results } = await api.search(query);
              setState((s) => ({
                ...s,
                streaming: false,
                matches: results.length ? results : null,
                error: results.length
                  ? null
                  : `Nothing found for "${query}". Try a DOI or a fuller title.`,
              }));
            } catch (e) {
              setState((s) => ({
                ...s,
                streaming: false,
                error: e instanceof ApiError ? e.message : String(e),
              }));
            }
          } else {
            setState((s) => ({ ...s, streaming: false, error: message }));
          }
        },
      });
    },
    [],
  );

  /** Re-query a single source and fold the answer into the preview on screen. */
  const retry = useCallback(
    async (source: string) => {
      const token = state.preview?.token;
      if (!token) return;
      setState((s) => ({
        ...s,
        panels: { ...s.panels, [source]: { ...s.panels[source], status: "pending", detail: "retrying" } },
      }));
      try {
        const next = await api.refetch(token, source);
        setState((s) => ({
          ...s,
          preview: next,
          panels: next.sources,
          merged: next.merged,
          versions: next.versions ?? [],
          refs: {
            loading: false,
            count: next.counts.references,
            note: next.edge_notes?.references ?? "",
            sample: next.sample_references,
          },
          cites: {
            loading: false,
            count: next.counts.citations,
            note: next.edge_notes?.citations ?? "",
            sample: next.sample_citations,
          },
        }));
      } catch (e) {
        setState((s) => ({ ...s, error: e instanceof ApiError ? e.message : String(e) }));
      }
    },
    [state.preview?.token],
  );

  return { ...state, lookup, retry, reset, setState };
}
