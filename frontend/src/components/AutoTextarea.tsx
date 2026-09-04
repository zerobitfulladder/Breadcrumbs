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

    // Collapse to nothing, and stop the parent from stretching it while we
    // look. scrollHeight reports the larger of the text and the box's own used
    // height, so any container that forces a height — a grid cell, which
    // stretches its items by default, or a column flex parent — makes the box
    // measure itself and stay at whatever it already was. Clamped to maxRows,
    // that is a permanently full-height box, which is what this looked like.
    const borrowed = {
      height: el.style.height,
      minHeight: el.style.minHeight,
      flexGrow: el.style.flexGrow,
      flexBasis: el.style.flexBasis,
      alignSelf: el.style.alignSelf,
    };
    el.style.height = "0px";
    el.style.minHeight = "0px";
    el.style.flexGrow = "0";
    el.style.flexBasis = "auto";
    el.style.alignSelf = "flex-start";   // "start" in grid: never stretch

    const content = el.scrollHeight - padding;

    el.style.minHeight = borrowed.minHeight;
    el.style.flexGrow = borrowed.flexGrow;
    el.style.flexBasis = borrowed.flexBasis;
    el.style.alignSelf = borrowed.alignSelf;
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
