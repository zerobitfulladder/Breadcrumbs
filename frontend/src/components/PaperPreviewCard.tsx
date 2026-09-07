import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { SaveResult } from "../api";
import { usePreviewStream } from "../hooks/usePreviewStream";
import { notifyChange } from "../live";
import PaperPreviewBody from "./PaperPreviewBody";
import "./PaperPreviewCard.css";

interface Props {
  /** DOI, OpenAlex id or any other identifier the backend understands. */
  identifier: string;
  titleHint?: string;
  /** Why it was suggested, when the assistant proposed it. */
  reason?: string;
  onClose: () => void;
  onSaved?: (result: SaveResult) => void;
}

/**
 * The Add page's lookup, shown as a card over the current page.
 *
 * The same preview, with the same Add button: a paper offered by the assistant
 * or found in a bibliography should be addable where you are looking at it,
 * rather than sending you to another tab to retype the identifier.
 */
export default function PaperPreviewCard({
  identifier,
  titleHint,
  reason,
  onClose,
  onSaved,
}: Props) {
  const s = usePreviewStream();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SaveResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    s.lookup(identifier, titleHint);
    // Re-run only when the paper changes, not on every render of the hook.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identifier, titleHint]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** Switching to another record of the same work re-runs the lookup here. */
  const useVersion = useCallback(
    (nextIdentifier: string, title: string) => {
      setSaved(null);
      setSaveError(null);
      s.lookup(nextIdentifier, title);
    },
    [s],
  );

  async function commit() {
    if (!s.preview) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.save(s.preview.token);
      setSaved(result);
      notifyChange({ kind: "paper", id: result.paper_id, source: "preview-card" });
      onSaved?.(result);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="ppc-backdrop" onPointerDown={onClose} role="dialog" aria-modal="true">
      <div className="ppc-card" onPointerDown={(e) => e.stopPropagation()}>
        <div className="ppc-bar">
          <span className="ppc-kicker">
            {saved ? "Added to your library" : "Preview · nothing is saved yet"}
          </span>
          {reason && <span className="ppc-reason">{reason}</span>}
          <button className="ppc-close" onClick={onClose} aria-label="Close the preview">
            Close
          </button>
        </div>
        <div className="ppc-body">
          {saveError && <div className="add-error">{saveError}</div>}
          {s.error ? (
            <div className="add-error">{s.error}</div>
          ) : (
            <PaperPreviewBody
              order={s.order}
              panels={s.panels}
              merged={s.merged}
              versions={s.versions}
              versionsError={s.versionsError}
              refs={s.refs}
              preview={s.preview}
              streaming={s.streaming}
              disabled={saving}
              onRetry={s.retry}
              onUseVersion={useVersion}
              actions={
                saved ? (
                  <span className="ppc-saved">
                    Added · {saved.links_created} link
                    {saved.links_created === 1 ? "" : "s"} created
                  </span>
                ) : (
                  <button
                    className="primary"
                    onClick={commit}
                    disabled={!s.preview || saving || s.streaming}
                  >
                    {saving
                      ? "Adding…"
                      : s.streaming
                        ? "Fetching…"
                        : s.preview?.already_in_library
                          ? "Update this paper"
                          : "Add to library"}
                  </button>
                )
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
