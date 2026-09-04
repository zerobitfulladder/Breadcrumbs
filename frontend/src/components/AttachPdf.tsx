import { useRef, useState } from "react";
import { api, ApiError } from "../api";
import { notifyChange } from "../live";

interface Props {
  paperId: number;
  /** Whether a file is already stored, which decides the wording. */
  hasPdf: boolean;
}

/**
 * Attach a PDF you already have to a paper.
 *
 * Most of this library is paywalled and no download route will ever reach it,
 * so a file on disk is the normal case rather than the fallback. The server
 * checks the magic bytes, so a mislabelled file is refused there rather than
 * stored and failing later in the reader.
 */
export default function AttachPdf({ paperId, hasPdf }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function attach(file: File) {
    setBusy(true);
    setError(null);
    try {
      await api.uploadPdf(paperId, file);
      // The badge, the reader and the timeline all read this; one announcement
      // refreshes every one of them.
      notifyChange({ kind: "paper", id: paperId, source: "upload" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        className="app-attach"
        disabled={busy}
        onClick={() => input.current?.click()}
        title={hasPdf ? "Replace the stored PDF" : "Attach a PDF from your computer"}
      >
        {busy ? "Adding…" : hasPdf ? "Replace PDF" : "Add PDF"}
      </button>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Cleared so picking the same file twice still fires a change.
          e.target.value = "";
          if (file) void attach(file);
        }}
      />
      {error && <p className="app-attach-error">{error}</p>}
    </>
  );
}
