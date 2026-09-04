import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { Suggestion } from "../api";
import "./SearchBox.css";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Fired on Enter, or when a suggestion is chosen. */
  onSubmit: (identifier: string, titleHint?: string) => void;
  disabled?: boolean;
  busy?: boolean;
}

const DEBOUNCE_MS = 220;
const MIN_CHARS = 2;

export default function SearchBox({ value, onChange, onSubmit, disabled, busy }: Props) {
  const [items, setItems] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [looksLikeId, setLooksLikeId] = useState(false);
  const [loading, setLoading] = useState(false);

  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** Set while a suggestion is being applied, so the effect does not refetch. */
  const skipNext = useRef(false);

  // debounced lookup, with the previous request cancelled on every keystroke
  useEffect(() => {
    if (skipNext.current) {
      skipNext.current = false;
      return;
    }
    const query = value.trim();
    abortRef.current?.abort();

    if (query.length < MIN_CHARS) {
      setItems([]);
      setOpen(false);
      setLooksLikeId(false);
      setLoading(false);
      return;
    }

    const timer = setTimeout(async () => {
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      try {
        const resp = await api.autocomplete(query, controller.signal);
        if (controller.signal.aborted) return;
        setLooksLikeId(resp.identifier != null);
        setItems(resp.results);
        setActive(-1);
        setOpen(resp.results.length > 0);
      } catch {
        // Aborted or upstream hiccup: typeahead stays silent by design.
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [value]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // click outside closes the list
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const choose = useCallback(
    (item: Suggestion) => {
      skipNext.current = true;
      onChange(item.title);
      setOpen(false);
      setItems([]);
      setActive(-1);
      // The id is exact, so look up by that; the title rides along so the
      // backend can still resolve the paper if that id lookup fails.
      onSubmit(item.s2_id, item.title);
    },
    [onChange, onSubmit],
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!items.length) return;
      e.preventDefault();
      setOpen(true);
      // Index -1 means "nothing selected", so the cycle runs
      // -1 -> 0 -> ... -> last -> -1, letting you step back out to your typing.
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((i) => {
        const next = i + step;
        if (next >= items.length) return -1;
        if (next < -1) return items.length - 1;
        return next;
      });
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (open && active >= 0 && active < items.length) choose(items[active]);
      else if (value.trim()) {
        setOpen(false);
        onSubmit(value.trim());
      }
      return;
    }
    if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  }

  return (
    <div className="sb-root" ref={rootRef}>
      <div className="sb-field">
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => items.length && setOpen(true)}
          placeholder="Title, DOI, arXiv id, or a URL containing one"
          spellCheck={false}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          disabled={disabled}
          autoFocus
        />
        {loading && <span className="sb-spinner" aria-hidden />}
        <button
          type="button"
          onClick={() => value.trim() && onSubmit(value.trim())}
          disabled={disabled || !value.trim()}
        >
          {busy ? "Fetching…" : "Look up"}
        </button>
      </div>

      {looksLikeId && (
        <div className="sb-hint">Recognised as an identifier. Press Enter to fetch it.</div>
      )}

      {open && items.length > 0 && (
        <ul className="sb-list" id={listId} role="listbox">
          {items.map((item, i) => (
            <li
              key={item.s2_id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "is-active" : ""}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(item)}
            >
              <span className="sb-title">{item.title}</span>
              <span className="sb-meta">
                {item.authors_year}
                {item.in_library && <span className="sb-badge">in library</span>}
              </span>
            </li>
          ))}
          <li className="sb-foot" aria-hidden>
            Semantic Scholar returns at most 10 suggestions. Press Enter to search fully.
          </li>
        </ul>
      )}
    </div>
  );
}
