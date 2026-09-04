import { useEffect, useMemo, useRef, useState } from "react";
import type { AiModel } from "../api";

interface Props {
  models: AiModel[] | undefined;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * Token prices as dollars per million, which is how they are always quoted.
 *
 * Providers publish dollars per token, so the raw figure is a string of
 * leading zeros that says nothing at a glance.
 */
export function perMillion(price?: string | null): string {
  // A missing price is unknown, not zero. Number(null) and Number("") are both
  // 0, which would advertise every unpriced model as free.
  if (price === null || price === undefined || price === "") return "";
  const n = Number(price);
  if (!Number.isFinite(n) || n < 0) return "";
  if (n === 0) return "free";
  const m = n * 1e6;
  return `$${m < 100 ? m.toFixed(2) : Math.round(m)}`;
}

/** Every token has to appear somewhere in the id or the name. */
function matches(model: AiModel, tokens: string[]): boolean {
  if (!tokens.length) return true;
  const hay = `${model.id} ${model.name ?? ""}`.toLowerCase();
  return tokens.every((t) => hay.includes(t));
}

/**
 * A model chooser you can type into.
 *
 * A plain select is unusable against OpenRouter, which lists hundreds of
 * models: finding one means scrolling a list ordered by nothing in particular.
 * Typing narrows it instead. Tokens are matched independently so "gpt 4o" and
 * "4o gpt" both find `openai/gpt-4o`, which a substring match would not.
 */
export default function ModelPicker({ models, value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const shown = useMemo(() => {
    const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
    return (models ?? []).filter((m) => matches(m, tokens));
  }, [models, query]);

  // Clamp when the filter shrinks the list under the cursor.
  useEffect(() => {
    setCursor((c) => Math.min(c, Math.max(0, shown.length - 1)));
  }, [shown.length]);

  // Keep the keyboard cursor in view; arrowing past the fold is otherwise blind.
  useEffect(() => {
    if (!open) return;
    list.current?.children[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  // Close on a click anywhere else. Blur alone is not enough: clicking an
  // option blurs the input before the click lands.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  function choose(id: string) {
    onChange(id);
    setQuery("");
    setOpen(false);
  }

  const placeholder = models
    ? value || "search models…"
    : value || "load the list first";

  return (
    <div className={`mp-root${disabled ? " is-disabled" : ""}`} ref={root}>
      <input
        className="mp-input"
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        disabled={disabled}
        value={open ? query : value}
        placeholder={placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setCursor(0);
          setOpen(true);
        }}
        onFocus={(e) => {
          setQuery("");
          setCursor(0);
          setOpen(true);
          e.target.select();
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            if (!open) return setOpen(true);
            const step = e.key === "ArrowDown" ? 1 : -1;
            setCursor((c) => Math.min(shown.length - 1, Math.max(0, c + step)));
          } else if (e.key === "Enter") {
            if (!open || !shown[cursor]) return;
            e.preventDefault();
            choose(shown[cursor].id);
          } else if (e.key === "Escape") {
            // First press abandons the search, a second leaves the field.
            if (open) e.stopPropagation();
            setOpen(false);
            setQuery("");
          }
        }}
      />
      {value && !open && !disabled && (
        <button
          type="button"
          className="mp-clear"
          title="Clear the chosen model"
          onClick={() => onChange("")}
        >
          ×
        </button>
      )}

      {open && (
        <ul className="mp-list" ref={list} role="listbox">
          {shown.map((m, i) => (
            <li
              key={m.id}
              role="option"
              aria-selected={m.id === value}
              className={`${i === cursor ? "is-cursor" : ""}${m.id === value ? " is-chosen" : ""}`}
              onMouseEnter={() => setCursor(i)}
              // mousedown, not click: the input's blur would close the list first.
              onMouseDown={(e) => {
                e.preventDefault();
                choose(m.id);
              }}
            >
              <span className="mp-id">{m.id}</span>
              <span className="mp-meta">
                {[
                  m.context ? `${Math.round(m.context / 1000)}k` : "",
                  perMillion(m.prompt_price),
                  m.reasoning ? "thinks" : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </li>
          ))}
          {!shown.length && (
            <li className="mp-none">
              {models?.length
                ? `Nothing matches "${query}".`
                : "No models loaded yet — press Load models."}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
