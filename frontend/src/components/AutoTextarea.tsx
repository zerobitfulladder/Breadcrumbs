import { useImperativeHandle, useLayoutEffect, useRef } from "react";

type Props = Omit<React.ComponentPropsWithoutRef<"textarea">, "rows"> & {
  /** Forwarded so callers can focus or select inside the box. */
  ref?: React.Ref<HTMLTextAreaElement>;
  /** Never shrink below this many lines. */
  minRows?: number;
  /** Scroll instead of growing past this many lines. */
  maxRows?: number;
};

/**
 * A textarea that is exactly as tall as its content.
 *
 * A fixed row count is wrong in both directions: it leaves a block of empty
 * space under a one-line note, and hides the end of a long one. Measuring on
 * every change costs a reflow and gets it right for both.
 */
export default function AutoTextarea({
  minRows = 1,
  maxRows = 14,
  ref: forwarded,
  ...rest
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(forwarded, () => ref.current as HTMLTextAreaElement, []);

  const resize = () => {
    const el = ref.current;
    if (!el) return;
    const style = window.getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || 16;
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);

    // Collapse to nothing first. "auto" can resolve against the parent in a
    // flex or grid row, which reports the row's height as the content height
    // and pins the box open at its maximum.
    el.style.height = "0px";
    const content = el.scrollHeight - padding;
    const clamped = Math.min(Math.max(content, line * minRows), line * maxRows);
    el.style.height = `${clamped + padding + border}px`;
    el.style.overflowY = content > line * maxRows ? "auto" : "hidden";
  };

  // Runs on value changes and on mount, before paint, so there is no flicker.
  useLayoutEffect(resize);

  return (
    <textarea
      {...rest}
      ref={ref}
      rows={minRows}
      onInput={(e) => {
        resize();
        rest.onInput?.(e);
      }}
    />
  );
}
