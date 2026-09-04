import { useEffect, useRef, useState } from "react";
import "./CopyChip.css";

interface Props {
  /** What lands on the clipboard. */
  value: string;
  /** What is shown, when that differs from the value. */
  label?: string;
  /** A short name for what this is, e.g. "DOI". */
  prefix?: string;
  title?: string;
  className?: string;
}

/**
 * Writes text to the clipboard, with a fallback for insecure origins.
 *
 * `navigator.clipboard` needs a secure context. localhost counts as one, but
 * reaching the dev server over a LAN address does not, and there the modern
 * API is simply absent — hence the old selection-based route behind it.
 */
async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const box = document.createElement("textarea");
    box.value = value;
    // Off-screen but still focusable: execCommand only copies a live selection.
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(box);
    return ok;
  } catch {
    return false;
  }
}

/** A value you click to copy, with a moment of confirmation. */
export default function CopyChip({ value, label, prefix, title, className }: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);

  // The confirmation is on a timer, so it must not outlive the component.
  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <button
      type="button"
      className={`cc-chip${className ? ` ${className}` : ""} is-${state}`}
      title={title ?? `Copy ${value}`}
      onClick={async (e) => {
        e.stopPropagation();
        setState((await copyText(value)) ? "copied" : "failed");
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setState("idle"), 1300);
      }}
    >
      {prefix && <span className="cc-prefix">{prefix}</span>}
      <span className="cc-value">{label ?? value}</span>
      <span className="cc-note">
        {state === "copied" ? "copied" : state === "failed" ? "copy failed" : "copy"}
      </span>
    </button>
  );
}
