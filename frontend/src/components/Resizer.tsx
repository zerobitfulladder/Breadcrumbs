import { useEffect, useRef, useState } from "react";
import "./Resizer.css";

interface Props {
  /** Which side of the divider the panel sits on. */
  side: "left" | "right";
  width: number;
  onChange: (width: number) => void;
  min?: number;
  max?: number;
}

/**
 * A drag handle between a fixed-width panel and the flexible middle.
 *
 * Pointer capture keeps the drag alive when the pointer outruns the handle,
 * which it always does — the handle is a few pixels wide and a resize is a
 * fast gesture.
 */
export default function Resizer({ side, width, onChange, min = 200, max = 900 }: Props) {
  const drag = useRef<{ x: number; start: number } | null>(null);
  const [active, setActive] = useState(false);

  return (
    <div
      className={`rz-grip${active ? " is-active" : ""}`}
      role="separator"
      aria-orientation="vertical"
      title="Drag to resize"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, start: width };
        setActive(true);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const delta = e.clientX - d.x;
        // A panel on the right grows as the pointer moves left.
        const next = side === "left" ? d.start + delta : d.start - delta;
        onChange(Math.min(max, Math.max(min, next)));
      }}
      onPointerUp={(e) => {
        if (drag.current) (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
        drag.current = null;
        setActive(false);
      }}
      onDoubleClick={() => onChange(side === "left" ? 264 : 384)}
    />
  );
}

/** Panel width remembered across sessions, under its own key. */
export function useStoredWidth(key: string, fallback: number) {
  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(key));
    return Number.isFinite(saved) && saved > 0 ? saved : fallback;
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, String(width));
    } catch {
      /* not remembering a width is not worth an error */
    }
  }, [key, width]);

  return [width, setWidth] as const;
}
