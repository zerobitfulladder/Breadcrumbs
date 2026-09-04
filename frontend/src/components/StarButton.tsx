import { useEffect, useState } from "react";
import { api } from "../api";
import { notifyChange } from "../live";
import "./StarButton.css";

interface Props {
  kind: "paper" | "author";
  id: number;
  starred: boolean;
  /** Shrinks it to sit inside a list row rather than a panel heading. */
  compact?: boolean;
}

/**
 * Star a paper or an author.
 *
 * Toggled optimistically: the star is the feedback, and waiting for a round
 * trip to fill it in makes a click feel unregistered. A failed write puts it
 * back rather than leaving the view claiming something the database does not.
 */
export default function StarButton({ kind, id, starred, compact }: Props) {
  const [on, setOn] = useState(starred);

  // The parent refetches after any change, including one made in another tab.
  useEffect(() => setOn(starred), [starred, id]);

  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();          // a row click selects; the star must not
    const next = !on;
    setOn(next);
    try {
      if (kind === "paper") await api.patchPaper(id, { favorite: next });
      else await api.patchAuthor(id, { favorite: next });
      notifyChange({ kind, id, source: "star" });
    } catch {
      setOn(!next);
    }
  }

  return (
    <button
      type="button"
      className={`sb-star${compact ? " is-compact" : ""}${on ? " is-on" : ""}`}
      onClick={toggle}
      aria-pressed={on}
      title={on ? "Remove from favourites" : "Add to favourites"}
    >
      {on ? "★" : "☆"}
    </button>
  );
}
