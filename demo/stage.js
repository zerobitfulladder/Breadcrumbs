/*
 * The stage: everything in the video that is not the app.
 *
 * Injected into every page before the app's own scripts, it lays a layer over
 * the app for the cursor, captions, title cards and the speed badge, and moves
 * a camera by transforming #root. The layer sits outside #root, so captions
 * stay put and sharp while the app zooms underneath them.
 *
 * Driven from record.mjs through window.__stage.
 */
(() => {
  if (window.top !== window || window.__stage) return;

  /**
   * How much slower than the video the page runs while it is recorded.
   * DevTools slows the CSS animations; anything timed here in script is
   * stretched by hand, so the two stay in step.
   */
  const SLOW = window.__stageSlow || 1;

  const CSS = `
    /* Clipped rather than hidden: a zoomed #root is larger than the window,
       and focusing a field off screen would otherwise scroll the document
       out from under the camera. */
    html { overflow: hidden !important; }
    body { overflow: clip !important; }
    #root {
      transform-origin: 0 0;
      transition: transform var(--cam-ms, 1000ms) cubic-bezier(.5, 0, .12, 1);
    }

    .stg-layer {
      position: fixed; inset: 0; z-index: 2147483000; pointer-events: none;
      font-family: "Space Grotesk", "Noto Sans", system-ui, sans-serif;
      --neon: #ff2a6d; --cyan: #05d9e8; --ink: #0a0418; --fg: #e6eaf8;
    }

    .stg-cursor {
      position: absolute; left: 0; top: 0; width: 28px; height: 28px;
      transform: translate(-100px, -100px); opacity: 0;
      transition: opacity .3s;
      filter: drop-shadow(0 3px 5px rgb(0 0 0 / .6)) drop-shadow(0 0 12px rgb(255 42 109 / .4));
    }
    /* Leans into the direction of travel and rights itself on stopping. */
    .stg-tilt { width: 100%; height: 100%; transform-origin: 3px 2px; transition: transform .16s ease-out; }
    .stg-cursor svg {
      display: block; width: 100%; height: 100%; transform-origin: 3px 2px;
      transition: transform .1s cubic-bezier(.3, 1.6, .5, 1);
    }
    .stg-cursor.is-down svg { transform: scale(.8); }
    .stg-ripple {
      position: absolute; width: 44px; height: 44px; margin: -22px 0 0 -22px;
      border-radius: 50%; border: 2px solid var(--neon);
      box-shadow: 0 0 18px var(--neon), inset 0 0 12px rgb(255 42 109 / .5);
      animation: stg-ripple .55s ease-out forwards;
    }
    @keyframes stg-ripple {
      from { transform: scale(.25); opacity: 1; }
      to   { transform: scale(1.35); opacity: 0; }
    }

    /* Bottom left, the lower third's usual place: the app keeps its panels
       and Bread on the right, so this side is the one free to cover. */
    .stg-caption {
      position: absolute; left: 40px; bottom: 40px;
      transform: translateY(24px); opacity: 0;
      transition: transform .55s cubic-bezier(.2, .9, .25, 1), opacity .45s;
      display: flex; gap: 18px; align-items: stretch;
      max-width: 900px; padding: 18px 30px 18px 22px;
      background: rgb(10 4 24 / .86); color: var(--fg);
      border: 1px solid rgb(255 42 109 / .45);
      box-shadow: 0 18px 50px rgb(0 0 0 / .55), 0 0 32px rgb(255 42 109 / .18);
      backdrop-filter: blur(10px);
    }
    .stg-caption.is-on { transform: none; opacity: 1; }
    .stg-caption::before {
      content: ""; flex: 0 0 4px; background: var(--neon);
      box-shadow: 0 0 12px var(--neon);
    }
    .stg-caption-title { font-size: 27px; font-weight: 600; letter-spacing: -.01em; white-space: nowrap; }
    .stg-caption-sub { margin-top: 5px; font-size: 16px; color: #a0a8c8; white-space: nowrap; }

    .stg-card {
      position: absolute; inset: 0; display: grid; place-content: center; text-align: center;
      background: radial-gradient(ellipse at 50% 45%, rgb(30 24 56 / .82), rgb(10 4 24 / .96) 70%);
      backdrop-filter: blur(16px) saturate(1.2);
      opacity: 0; transition: opacity .8s;
      color: var(--fg);
    }
    .stg-card.is-on { opacity: 1; }
    .stg-card-mark {
      width: 64px; height: 64px; margin: 0 auto 26px;
      transform: scale(.7); opacity: 0; transition: transform .9s cubic-bezier(.2, .9, .25, 1.2) .1s, opacity .6s .1s;
    }
    .stg-card-title {
      font-size: 84px; font-weight: 700; letter-spacing: -.035em; line-height: 1;
      text-shadow: 0 0 26px rgb(255 42 109 / .55), 0 0 2px rgb(255 42 109 / .9);
      transform: translateY(18px); opacity: 0; transition: transform .9s cubic-bezier(.2, .9, .25, 1) .2s, opacity .7s .2s;
    }
    .stg-card-sub {
      margin-top: 22px; font-size: 26px; color: #a0a8c8; letter-spacing: .01em;
      transform: translateY(14px); opacity: 0; transition: transform .9s cubic-bezier(.2, .9, .25, 1) .45s, opacity .7s .45s;
    }
    .stg-card-sub b { color: var(--cyan); font-weight: 500; }
    .stg-card.is-on .stg-card-mark { transform: none; opacity: 1; }
    .stg-card.is-on .stg-card-title,
    .stg-card.is-on .stg-card-sub { transform: none; opacity: 1; }

    .stg-keys {
      position: absolute; left: 50%; bottom: 150px; display: flex; gap: 10px;
      transform: translate(-50%, 10px); opacity: 0; transition: transform .3s, opacity .3s;
    }
    .stg-keys.is-on { transform: translate(-50%, 0); opacity: 1; }
    .stg-key {
      min-width: 54px; padding: 10px 16px; text-align: center;
      font-size: 22px; font-weight: 600; color: var(--fg);
      background: rgb(30 24 56 / .92); border: 1px solid #5a4a78; border-bottom-width: 4px;
      box-shadow: 0 10px 30px rgb(0 0 0 / .5);
    }
    .stg-key.is-hit { animation: stg-hit .22s; border-color: var(--neon); }
    @keyframes stg-hit { 50% { transform: translateY(3px); border-bottom-width: 1px; } }

    .stg-badge {
      position: absolute; top: 22px; right: 22px; padding: 8px 14px;
      font: 600 15px/1 "Space Grotesk", "Noto Sans", sans-serif; letter-spacing: .04em;
      color: var(--ink); background: var(--cyan); box-shadow: 0 0 22px rgb(5 217 232 / .6);
      opacity: 0; transform: translateY(-8px); transition: opacity .3s, transform .3s;
    }
    .stg-badge.is-on { opacity: 1; transform: none; }

    .stg-cover {
      position: absolute; inset: 0; background: #0a0418; opacity: 0;
      transition: opacity var(--cover-ms, 500ms);
    }
    .stg-cover.is-on { opacity: 1; }
  `;

  // Breadcrumbs' own mark, drawn small: three crumbs on a trail.
  const MARK = `
    <svg viewBox="0 0 64 64" fill="none">
      <path d="M10 50 C 24 44, 22 22, 36 20 S 52 14, 56 10" stroke="#5a4a78" stroke-width="2" stroke-dasharray="3 5"/>
      <circle cx="10" cy="50" r="6" fill="#ff2a6d"/>
      <circle cx="33" cy="22" r="6" fill="#05d9e8"/>
      <circle cx="56" cy="10" r="6" fill="#fcee0c"/>
    </svg>`;

  const ARROW = `
    <svg viewBox="0 0 26 26">
      <path d="M3 2 L3 21 L8.2 16.4 L11.6 24 L15 22.5 L11.7 15 L18.6 15 Z"
            fill="#ffffff" stroke="#0a0418" stroke-width="1.6" stroke-linejoin="round"/>
    </svg>`;

  const els = {};
  // Off for title cards, where an arrow resting on the name reads as a stray.
  let pointerOff = false;
  const cam = { s: 1, x: 0, y: 0 };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms * SLOW));

  // --- smooth scrolling -----------------------------------------------------

  /*
   * Smooth scrolls, the app's and ours, run here rather than in Chrome.
   * Chrome animates them in real time, which slow motion would turn into a
   * jump; a scroll stepped by hand can be slowed like everything else.
   */
  const nativeScrollTo = Element.prototype.scrollTo;
  const nativeScrollBy = Element.prototype.scrollBy;
  const scrolling = new WeakMap();

  function glide(el, left, top) {
    const maxTop = el.scrollHeight - el.clientHeight;
    const maxLeft = el.scrollWidth - el.clientWidth;
    const to = {
      top: Math.max(0, Math.min(maxTop, top)),
      left: Math.max(0, Math.min(maxLeft, left)),
    };
    const from = { top: el.scrollTop, left: el.scrollLeft };
    const ms = 480 * SLOW;
    const start = performance.now();
    const token = {};
    scrolling.set(el, token);
    // performance.now(), not the frame's own timestamp: with DevTools slowing
    // animations, frame timestamps run behind it, and mixing the two clocks
    // sent the scroll backwards to the top.
    const step = () => {
      if (scrolling.get(el) !== token) return;
      const t = Math.min(1, Math.max(0, (performance.now() - start) / ms));
      const e = 1 - (1 - t) ** 3;
      nativeScrollTo.call(el, {
        top: from.top + (to.top - from.top) * e,
        left: from.left + (to.left - from.left) * e,
        behavior: "instant",
      });
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  Element.prototype.scrollTo = function (a, ...rest) {
    if (a && typeof a === "object" && a.behavior === "smooth") {
      return glide(this, a.left ?? this.scrollLeft, a.top ?? this.scrollTop);
    }
    return nativeScrollTo.call(this, a, ...rest);
  };
  Element.prototype.scrollBy = function (a, ...rest) {
    if (a && typeof a === "object" && a.behavior === "smooth") {
      return glide(this, this.scrollLeft + (a.left ?? 0), this.scrollTop + (a.top ?? 0));
    }
    return nativeScrollBy.call(this, a, ...rest);
  };

  function make(cls, html = "") {
    const el = document.createElement("div");
    el.className = cls;
    el.innerHTML = html;
    els.layer.appendChild(el);
    return el;
  }

  function mount() {
    const font = document.createElement("link");
    font.rel = "stylesheet";
    font.href = "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=block";
    document.head.appendChild(font);
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    els.layer = document.createElement("div");
    els.layer.className = "stg-layer";
    document.body.appendChild(els.layer);

    els.cover = make("stg-cover");
    els.card = make("stg-card");
    els.caption = make("stg-caption", `<div><div class="stg-caption-title"></div><div class="stg-caption-sub"></div></div>`);
    els.keys = make("stg-keys");
    els.badge = make("stg-badge");
    els.cursor = make("stg-cursor", `<div class="stg-tilt">${ARROW}</div>`);
    const tilt = els.cursor.firstElementChild;

    // The cursor follows real input: the mouse Playwright moves is the one the
    // app sees, so hover and drag behave exactly as they would by hand.
    let last = null;
    let rest = 0;
    window.addEventListener("mousemove", (e) => {
      els.cursor.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`;
      els.cursor.style.opacity = pointerOff ? "0" : "1";
      const now = performance.now();
      if (last) {
        // Horizontal speed in pixels per millisecond of video.
        const v = (e.clientX - last.x) / Math.max(1, (now - last.t) / SLOW);
        const lean = Math.max(-11, Math.min(11, v * 4.5));
        tilt.style.transform = `rotate(${lean}deg)`;
      }
      last = { x: e.clientX, t: now };
      clearTimeout(rest);
      rest = setTimeout(() => { tilt.style.transform = ""; }, 90 * SLOW);
    }, true);
    window.addEventListener("mousedown", (e) => {
      els.cursor.classList.add("is-down");
      const ring = make("stg-ripple");
      ring.style.left = `${e.clientX}px`;
      ring.style.top = `${e.clientY}px`;
      setTimeout(() => ring.remove(), 700 * SLOW);
    }, true);
    window.addEventListener("mouseup", () => els.cursor.classList.remove("is-down"), true);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });

  // --- camera ---------------------------------------------------------------

  function applyCamera(ms) {
    const root = document.getElementById("root");
    if (!root) return;
    document.scrollingElement?.scrollTo(0, 0);
    root.style.setProperty("--cam-ms", `${ms}ms`);
    root.style.transform = `translate(${cam.x}px, ${cam.y}px) scale(${cam.s})`;
  }

  /** A client rectangle, as it would be with the camera at rest. */
  function unzoomed(r) {
    return {
      x: (r.left - cam.x) / cam.s,
      y: (r.top - cam.y) / cam.s,
      w: r.width / cam.s,
      h: r.height / cam.s,
    };
  }

  function resolveRect(target) {
    if (typeof target === "string") {
      const el = document.querySelector(target);
      if (!el) throw new Error(`stage: nothing matches ${target}`);
      return unzoomed(el.getBoundingClientRect());
    }
    if (target instanceof Element) return unzoomed(target.getBoundingClientRect());
    // A plain rectangle is taken to be in client coordinates, like a bounding box.
    return unzoomed({ left: target.x, top: target.y, width: target.w, height: target.h });
  }

  /**
   * Frame a target. It fills `fill` of the screen unless `scale` says how far
   * to zoom, and the view never leaves the page, so no edge of nothing shows.
   */
  function focus(target, { scale, fill = 0.72, max = 2.4, ms = 1000, dx = 0, dy = 0 } = {}) {
    const r = resolveRect(target);
    const W = innerWidth;
    const H = innerHeight;
    const s = Math.max(1, scale ?? Math.min(max, (W * fill) / r.w, (H * fill) / r.h));
    const cx = r.x + r.w / 2 + dx;
    const cy = r.y + r.h / 2 + dy;
    cam.s = s;
    cam.x = Math.min(0, Math.max(W - W * s, W / 2 - cx * s));
    cam.y = Math.min(0, Math.max(H - H * s, H / 2 - cy * s));
    applyCamera(ms);
    return ms;
  }

  function reset(ms = 900) {
    cam.s = 1;
    cam.x = 0;
    cam.y = 0;
    applyCamera(ms);
    return ms;
  }

  // --- titles ---------------------------------------------------------------

  function caption(title, sub = "") {
    const box = els.caption;
    if (!title) {
      box.classList.remove("is-on");
      return;
    }
    const swap = () => {
      box.querySelector(".stg-caption-title").textContent = title;
      const subEl = box.querySelector(".stg-caption-sub");
      subEl.textContent = sub;
      subEl.style.display = sub ? "" : "none";
      box.classList.add("is-on");
    };
    // Changing captions drops the old one first, so the two never blur together.
    if (box.classList.contains("is-on")) {
      box.classList.remove("is-on");
      setTimeout(swap, 380 * SLOW);
    } else swap();
  }

  function card(title, sub = "") {
    const box = els.card;
    if (!title) {
      box.classList.remove("is-on");
      return;
    }
    box.innerHTML = `
      <div class="stg-card-mark">${MARK}</div>
      <div class="stg-card-title"></div>
      <div class="stg-card-sub"></div>`;
    box.querySelector(".stg-card-title").textContent = title;
    // Sub lines may carry <b> for a coloured word; they are written here, never
    // taken from the page.
    box.querySelector(".stg-card-sub").innerHTML = sub;
    void box.offsetWidth;
    box.classList.add("is-on");
  }

  async function keys(labels, gap = 170) {
    const box = els.keys;
    box.innerHTML = "";
    box.classList.add("is-on");
    for (const label of labels) {
      const k = document.createElement("div");
      k.className = "stg-key is-hit";
      k.textContent = label;
      box.appendChild(k);
      await sleep(gap);
    }
  }

  function hideKeys() {
    els.keys.classList.remove("is-on");
  }

  function badge(text) {
    if (text) els.badge.textContent = text;
    els.badge.classList.toggle("is-on", Boolean(text));
  }

  /** Show or hide the cursor; while hidden, moving the mouse does not bring it back. */
  function pointer(on) {
    pointerOff = !on;
    els.cursor.style.opacity = on ? "1" : "0";
  }

  /** Take the whole layer away, for a screenshot of the app alone. */
  function hidden(on) {
    els.layer.style.display = on ? "none" : "";
  }

  function cover(on, ms = 500) {
    els.cover.style.setProperty("--cover-ms", `${ms}ms`);
    els.cover.classList.toggle("is-on", on);
    return ms;
  }

  // --- the PDF --------------------------------------------------------------

  /**
   * Find a phrase in a page's text layer.
   *
   * Whitespace is ignored on both sides: pdf.js splits lines into spans and
   * drops the space at a line break, so a sentence that wraps never matches
   * character for character.
   */
  function findText(pageNo, phrase) {
    const layer = document.querySelector(`.pv-page[data-page="${pageNo}"] .textLayer`);
    if (!layer) return null;
    const chars = [];
    let flat = "";
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n.nodeValue;
      for (let i = 0; i < t.length; i += 1) {
        if (/\s/.test(t[i])) continue;
        flat += t[i];
        chars.push([n, i]);
      }
    }
    const want = phrase.replace(/\s+/g, "");
    const at = flat.indexOf(want);
    if (at < 0) return null;
    const [sn, so] = chars[at];
    const [en, eo] = chars[at + want.length - 1];
    const range = document.createRange();
    range.setStart(sn, so);
    range.setEnd(en, eo + 1);
    return { range, sn, so, en, eo };
  }

  function charRect(node, offset) {
    const r = document.createRange();
    r.setStart(node, offset);
    r.setEnd(node, offset + 1);
    return r.getBoundingClientRect();
  }

  /** Where to press and release the mouse to select a phrase, in client pixels. */
  function textPoints(pageNo, phrase) {
    const hit = findText(pageNo, phrase);
    if (!hit) return null;
    const a = charRect(hit.sn, hit.so);
    const b = charRect(hit.en, hit.eo);
    const all = hit.range.getBoundingClientRect();
    return {
      start: { x: a.left + 0.5, y: a.top + a.height / 2 },
      end: { x: b.right - 0.5, y: b.top + b.height / 2 },
      box: { x: all.left, y: all.top, w: all.width, h: all.height },
    };
  }

  /**
   * Select a phrase exactly, for when a drag lands a word off.
   *
   * The reader reads the selection on pointerup, so one is sent afterwards,
   * the same event a hand releasing the button would send.
   */
  function selectText(pageNo, phrase) {
    const hit = findText(pageNo, phrase);
    if (!hit) return false;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(hit.range);
    const b = charRect(hit.en, hit.eo);
    document.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true, clientX: b.right, clientY: b.bottom,
    }));
    return true;
  }

  /** Scroll a container so a child sits at its top, at any camera zoom. */
  function scrollToChild(container, child, offset = 8) {
    const box = document.querySelector(container);
    const kids = box ? box.querySelectorAll(child) : [];
    const el = kids[kids.length - 1];
    if (!el) return false;
    const by = (el.getBoundingClientRect().top - box.getBoundingClientRect().top) / cam.s - offset;
    box.scrollBy({ top: by, behavior: "smooth" });
    return true;
  }

  function selectionText() {
    return (window.getSelection()?.toString() ?? "").replace(/\s+/g, " ").trim();
  }

  window.__stage = {
    focus, reset, caption, card, keys, hideKeys, badge, cover, hidden, pointer,
    textPoints, selectText, selectionText, scrollToChild,
    camera: () => ({ ...cam }),
    fontsReady: () => document.fonts.ready.then(() => true),
  };
})();
