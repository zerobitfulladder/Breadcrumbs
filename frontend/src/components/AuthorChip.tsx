import { useRef, useState } from "react";
import { api } from "../api";
import type { Author } from "../api";
import "./AuthorChip.css";

interface Props {
  author: Author;
  onOpen: (authorId: number) => void;
}

const HOVER_DELAY_MS = 260;

/**
 * An author's name in a paper's byline: a link to their panel, with a portrait
 * on hover.
 *
 * The portrait comes with the paper when it has already been looked up. When
 * it has not, the lookup runs after a short hover — long enough that skimming
 * a byline does not fire a request per name.
 */
export default function AuthorChip({ author, onOpen }: Props) {
  const [thumb, setThumb] = useState<string | null>(author.thumbnail_url ?? null);
  const [description, setDescription] = useState<string | null>(
    author.profile_description ?? null,
  );
  const [shown, setShown] = useState(false);
  const [at, setAt] = useState<{ left: number; bottom: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | null>(null);
  const fetched = useRef(Boolean(author.thumbnail_url || author.profile_status));

  function enter(e: React.PointerEvent) {
    // Fixed to the viewport rather than the panel: the panel clips its
    // overflow, so an absolutely positioned card would be cut off.
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setAt({ left: box.left, bottom: window.innerHeight - box.top + 6 });
    setShown(true);
    if (fetched.current || !author.id) return;
    timer.current = window.setTimeout(async () => {
      fetched.current = true;
      setLoading(true);
      try {
        const p = await api.authorProfile(author.id!);
        setThumb(p.thumbnail_url ?? p.image_url ?? null);
        setDescription(p.description ?? null);
      } catch {
        /* no portrait is a normal outcome, not an error worth showing */
      } finally {
        setLoading(false);
      }
    }, HOVER_DELAY_MS);
  }

  function leave() {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setShown(false);
  }

  const affiliation =
    author.affiliation ?? author.institutions?.[0]?.name ?? null;
  const country = author.country ?? author.institutions?.[0]?.country_code ?? null;

  return (
    <span className="ac-wrap" onPointerEnter={enter} onPointerLeave={leave}>
      <button className="ac-name" onClick={() => author.id && onOpen(author.id)}>
        {author.name}
      </button>
      {affiliation && (
        <span className="ac-affil">
          {" · "}
          {affiliation}
          {country ? ` (${country})` : ""}
        </span>
      )}

      {shown && (
        <span
          className="ac-card"
          role="tooltip"
          style={at ? { left: at.left, bottom: at.bottom } : undefined}
        >
          {thumb ? (
            <img src={thumb} alt="" loading="lazy" />
          ) : (
            <span className="ac-blank">
              {loading
                ? "…"
                : author.name
                    .split(/\s+/)
                    .map((w) => w[0])
                    .filter((c) => /[A-Za-z]/.test(c ?? ""))
                    .slice(0, 2)
                    .join("")}
            </span>
          )}
          <span className="ac-meta">
            <strong>{author.name}</strong>
            {description && <span>{description}</span>}
            {affiliation && <span className="dim">{affiliation}</span>}
            {!description && !loading && fetched.current && !thumb && (
              <span className="dim">No biography found.</span>
            )}
          </span>
        </span>
      )}
    </span>
  );
}
