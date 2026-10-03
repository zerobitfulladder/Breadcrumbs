#!/usr/bin/env node
/**
 * Record the tour in tour.mjs as a video.
 *
 *   npm run record                    -> out/breadcrumbs-demo.mp4, 2560x1440 at 60 fps
 *   npm run record -- --size 1920x1080
 *   npm run record -- --watch         a visible browser, to rehearse
 *   npm run record -- --no-video      run the tour without recording, at normal speed
 *   npm run gif                       the citation trail, as docs/trail.gif
 *   npm run stills                    the README's screenshots, into docs/
 *
 * `--tour` picks the script: tour.mjs (the video), trail.mjs, stills.mjs. An
 * `--out` ending in .gif records as usual and converts at the end.
 *
 * Everything runs against a copy of the library made for this run, served on
 * its own port, so what the assistant writes during the tour never reaches
 * the real one. The copy holds your settings, API keys included, which is
 * what lets the assistant answer for real; it is deleted when the run ends.
 *
 * The tour is played in slow motion and the video at normal speed. Chrome
 * hands over frames of a page as busy as this one at 15 to 25 a second; slowed
 * fourfold, that is 60 to 100 a second of video, so motion comes out smooth.
 * Every animation in the page is slowed through DevTools, and every motion the
 * tour makes is slowed by the same factor, so nothing gives it away.
 */
import { spawn, spawnSync } from "node:child_process";
import { cp, mkdir, mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { DatabaseSync } from "node:sqlite";
import { chromium } from "playwright-core";

import { PAPER } from "./tour.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

const { values: opts } = parseArgs({
  options: {
    out: { type: "string", default: join(HERE, "out", "breadcrumbs-demo.mp4") },
    port: { type: "string", default: "8765" },
    library: { type: "string", default: process.env.BREADCRUMBS_HOME ?? join(homedir(), "Breadcrumbs") },
    watch: { type: "boolean", default: false },
    "no-video": { type: "boolean", default: false },
    fps: { type: "string", default: "60" },
    size: { type: "string", default: "2560x1440" },
    slow: { type: "string", default: "4" },
    tour: { type: "string", default: "tour" },
    "gif-fps": { type: "string", default: "20" },
  },
});

/**
 * The page is laid out at this size and drawn at twice it (2880x1620), then
 * scaled down to the video, which keeps text sharp at any output size up to
 * that.
 */
const VIEWPORT = { width: 1440, height: 810 };
const [VIDEO_W, VIDEO_H] = opts.size.split("x").map(Number);
const VIDEO = { width: VIDEO_W, height: VIDEO_H };

/** How much slower than the video the tour plays while it is recorded. */
const SLOW = opts["no-video"] ? 1 : Math.max(1, Number(opts.slow));

/** One frame of video, in milliseconds of video. */
const FRAME_MS = 1000 / Number(opts.fps);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- the library copy -------------------------------------------------------

async function copyLibrary(source) {
  if (!existsSync(join(source, "library.db"))) {
    throw new Error(`No library at ${source}. Pass --library or set BREADCRUMBS_HOME.`);
  }
  const home = await mkdtemp(join(tmpdir(), "breadcrumbs-demo-"));
  // VACUUM INTO rather than a file copy: a server running on the real library
  // may hold recent writes in the WAL, which a copy of the main file misses.
  const src = new DatabaseSync(join(source, "library.db"), { readOnly: true });
  src.exec(`VACUUM INTO '${join(home, "library.db").replaceAll("'", "''")}'`);
  src.close();
  for (const dir of ["pdfs", "authors", "notes"]) {
    if (existsSync(join(source, dir))) await cp(join(source, dir), join(home, dir), { recursive: true });
  }

  // Start the tour from a clean slate: no earlier conversations in the
  // assistant, and nothing yet marked in the paper the tour reads.
  const db = new DatabaseSync(join(home, "library.db"));
  db.exec("DELETE FROM chat_messages; DELETE FROM chat_sessions;");
  db.prepare("DELETE FROM highlights WHERE paper_id = ?").run(PAPER.id);
  db.prepare("UPDATE papers SET status = 'unread' WHERE id = ?").run(PAPER.id);
  db.close();
  return home;
}

// --- the server -------------------------------------------------------------

async function answering(url) {
  try {
    return (await fetch(url)).ok;
  } catch {
    return false;
  }
}

async function startServer(home, port) {
  const base = `http://127.0.0.1:${port}`;
  if (await answering(`${base}/api/settings`)) {
    throw new Error(`Something is already serving on port ${port}. Pass --port.`);
  }
  // Built here rather than left to `run`, which would serve the real library.
  const build = spawnSync(join(ROOT, "breadcrumbs"), ["build"], { cwd: ROOT, stdio: "inherit" });
  if (build.status !== 0) throw new Error("The frontend build failed.");

  const server = spawn(
    "uv",
    ["run", "--project", join(ROOT, "backend"), "uvicorn", "backend.api:app",
     "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
    { cwd: ROOT, env: { ...process.env, BREADCRUMBS_HOME: home }, stdio: ["ignore", "inherit", "inherit"] },
  );
  const deadline = Date.now() + 60_000;
  while (!(await answering(`${base}/api/settings`))) {
    if (server.exitCode !== null) throw new Error("The server stopped while starting.");
    if (Date.now() > deadline) throw new Error("The server did not answer within a minute.");
    await sleep(300);
  }
  return { base, stop: () => server.kill("SIGTERM") };
}

// --- the recorder -----------------------------------------------------------

/**
 * Frames from Chrome's screencast, laid onto a fixed-rate timeline.
 *
 * Chrome sends a frame only when something on screen changes, so each one is
 * held until the next arrives, which is what turns a still screen into still
 * video rather than a skipped one. Real time is divided by `slow` on the way
 * through, and by a further factor while the assistant is thinking, and
 * `pause` stops the clock altogether while a new tab loads.
 */
class Recorder {
  constructor(out, fps, slow) {
    this.out = out;
    this.fps = fps;
    this.slow = slow;
    this.speed = slow;
    this.paused = false;
    this.frame = null;
    this.clock = performance.now();
    this.elapsed = 0;   // seconds of video so far
    this.written = 0;   // frames written so far
    this.received = 0;  // frames Chrome sent
    this.cdp = null;
  }

  async start() {
    await mkdir(dirname(this.out), { recursive: true });
    // A GIF is cut from a video recorded as usual, kept until it is converted.
    this.gif = this.out.endsWith(".gif");
    this.video = this.gif ? `${this.out}.mp4` : this.out;
    const { width, height } = VIDEO;
    this.ffmpeg = spawn("ffmpeg", [
      "-y", "-loglevel", "error",
      "-f", "image2pipe", "-framerate", String(this.fps), "-c:v", "mjpeg", "-i", "-",
      "-vf", `scale=${width}:${height}:force_original_aspect_ratio=decrease:flags=lanczos,` +
             `pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=0x0a0418,` +
             // Lossless on the way to a GIF: the faint noise of a lossy encode
             // differs frame to frame, and the GIF would store it all as change.
             `format=${this.gif ? "yuv444p" : "yuv420p"}`,
      "-c:v", "libx264", "-preset", "medium", "-crf", this.gif ? "0" : "14", "-movflags", "+faststart",
      this.video,
    ], { stdio: ["pipe", "inherit", "inherit"] });
    this.done = new Promise((ok, fail) => {
      this.ffmpeg.on("close", (code) => (code === 0 ? ok() : fail(new Error(`ffmpeg exited ${code}`))));
    });
  }

  /** Move the timeline up to now, repeating the frame on screen. */
  advance() {
    const now = performance.now();
    // The timeline starts with the first frame, not with the recorder, so a
    // tour's setup can take as long as it likes without appearing.
    if (!this.frame) {
      this.clock = now;
      return;
    }
    if (!this.paused) this.elapsed += (now - this.clock) / 1000 / this.speed;
    this.clock = now;
    while (this.written < this.elapsed * this.fps) {
      this.ffmpeg.stdin.write(this.frame);
      this.written += 1;
    }
  }

  /** Play faster than life: 1 is normal speed in the video. */
  setSpeed(factor) {
    this.advance();
    this.speed = this.slow * factor;
  }

  pause() {
    this.advance();
    this.paused = true;
  }

  resume() {
    this.advance();
    this.paused = false;
  }

  async attach(page) {
    await this.detach();
    const cdp = await page.context().newCDPSession(page);
    // Slow every CSS transition and animation in the page by the same factor
    // as the tour, so the app's own motion keeps its speed in the video.
    await cdp.send("Animation.enable");
    await cdp.send("Animation.setPlaybackRate", { playbackRate: 1 / this.slow });
    cdp.on("Page.screencastFrame", ({ data, sessionId }) => {
      cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => undefined);
      this.advance();
      this.frame = Buffer.from(data, "base64");
      this.received += 1;
    });
    // Full size, no maxWidth: ffmpeg's lanczos scales down better than the
    // screencast's own resize.
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 100, everyNthFrame: 1 });
    this.cdp = cdp;
  }

  async detach() {
    if (!this.cdp) return;
    const cdp = this.cdp;
    this.cdp = null;
    await cdp.send("Page.stopScreencast").catch(() => undefined);
    await cdp.detach().catch(() => undefined);
  }

  async stop() {
    this.advance();
    await this.detach();
    this.ffmpeg.stdin.end();
    await this.done;
    if (this.gif) await this.toGif();
    return { seconds: this.elapsed, captured: this.received / this.elapsed };
  }

  /**
   * One palette for the whole loop, built from what changes between frames,
   * and only the changed rectangle written per frame, which keeps a dark page
   * with a few bright lines small. No dithering: the app is flat colour, and
   * dither noise is change the GIF has to store on every frame.
   */
  async toGif() {
    const fps = Number(opts["gif-fps"]);
    const convert = spawnSync("ffmpeg", [
      "-y", "-loglevel", "error", "-i", this.video,
      "-vf", `fps=${fps},split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];` +
             `[b][p]paletteuse=dither=none:diff_mode=rectangle`,
      "-loop", "0", this.out,
    ], { stdio: "inherit" });
    await rm(this.video, { force: true });
    if (convert.status !== 0) throw new Error("Converting to GIF failed.");
  }
}

/** Stands in for the recorder when there is nothing to record. */
const NO_RECORDER = {
  setSpeed() {}, pause() {}, resume() {}, async attach() {}, async detach() {},
};

// --- the director ---------------------------------------------------------

/** Minimum jerk: how a hand reaching for something speeds up and slows down. */
const minJerk = (t) => t * t * t * (10 - 15 * t + 6 * t * t);

/** A quick start and a long settle, the shape of a pointer reaching a target. */
const reach = (t) => minJerk(1 - (1 - t) ** 1.7);

/**
 * What the tour is written in: a hand on the mouse and keyboard, a camera, and
 * the titles. Every motion is real input to the real app; the stage only draws
 * a cursor where that input goes.
 *
 * Every duration here is in milliseconds of video. The director stretches them
 * by the slow-motion factor while recording, so the tour reads as if it ran in
 * real time.
 */
class Director {
  constructor(page, recorder, slow) {
    this.page = page;
    this.rec = recorder;
    this.slow = slow;
    this.pos = { x: VIEWPORT.width / 2, y: VIEWPORT.height / 2 };
  }

  /** Wait, in milliseconds of video. */
  wait(ms) {
    return sleep(ms * this.slow);
  }

  stage(name, ...args) {
    return this.page.evaluate(([n, a]) => window.__stage[n](...a), [name, args]);
  }

  // Titles.

  async caption(title, sub = "") {
    console.log(`  ${title}`);
    await this.stage("caption", title, sub);
  }

  async uncaption() {
    await this.stage("caption", null);
  }

  // Camera. Each waits out its own move, so whatever comes next measures the
  // page where it has settled.

  /** Frame a selector, a locator, or a rectangle in page pixels. */
  async focus(target, opts = {}) {
    const what =
      typeof target === "string" || !target.boundingBox ? target : await this.box(target);
    const ms = await this.stage("focus", what, opts);
    await this.wait(ms + 60);
  }

  async reset(ms = 900) {
    await this.stage("reset", ms);
    await this.wait(ms + 60);
  }

  // The hand.

  async box(locator) {
    await locator.first().waitFor({ state: "visible", timeout: 30_000 });
    const b = await locator.first().boundingBox();
    if (!b) throw new Error(`No box for ${locator}`);
    return { x: b.x, y: b.y, w: b.width, h: b.height };
  }

  /** Move the mouse along `at(t)`, one step per frame of video. */
  async trace(at, ms) {
    const frames = Math.max(3, Math.round(ms / FRAME_MS));
    for (let i = 1; i <= frames; i += 1) {
      const started = performance.now();
      const p = at(i / frames);
      await this.page.mouse.move(p.x, p.y);
      // Keep the cadence: a slow round trip comes out of the wait, not on top.
      await sleep(Math.max(0, FRAME_MS * this.slow - (performance.now() - started)));
    }
  }

  /**
   * Reach for a point the way a hand does: off quickly, along a slight curve,
   * a long settle, and on a long reach a small overshoot taken back.
   */
  async move(x, y, { ms } = {}) {
    const from = { ...this.pos };
    const dx = x - from.x;
    const dy = y - from.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 1) return;
    const duration = ms ?? Math.min(560, Math.max(200, 150 + dist * 0.3));
    const ux = dx / dist;
    const uy = dy / dist;
    const over = dist > 240 ? Math.min(12, dist * 0.022) : 0;
    const end = { x: x + ux * over, y: y + uy * over };
    const bend = (0.5 + Math.random() * 0.5) * Math.min(42, dist * 0.08) * (Math.random() < 0.5 ? -1 : 1);
    const ctrl = { x: from.x + dx / 2 - uy * bend, y: from.y + dy / 2 + ux * bend };

    await this.trace((t) => {
      const e = reach(t);
      const a = (1 - e) * (1 - e);
      const b = 2 * (1 - e) * e;
      const c = e * e;
      return { x: a * from.x + b * ctrl.x + c * end.x, y: a * from.y + b * ctrl.y + c * end.y };
    }, over ? duration * 0.85 : duration);
    if (over) {
      await this.trace((t) => {
        const e = minJerk(t);
        return { x: end.x + (x - end.x) * e, y: end.y + (y - end.y) * e };
      }, 110);
    }
    this.pos = { x, y };
  }

  async moveTo(locator, { dx = 0.5, dy = 0.5, ms } = {}) {
    const b = await this.box(locator);
    await this.move(b.x + b.w * dx, b.y + b.h * dy, { ms });
  }

  async hover(locator, hold = 700, opts = {}) {
    await this.moveTo(locator, opts);
    await this.wait(hold);
  }

  async click(locator, opts = {}) {
    await this.moveTo(locator, opts);
    await this.wait(70);
    await this.page.mouse.down();
    await this.wait(70);
    await this.page.mouse.up();
    await this.wait(opts.after ?? 350);
  }

  /** Scroll with the wheel, a small notch every frame, for `ms` of video. */
  async wheel(locator, deltaY, { ms = 600, ctrl = false } = {}) {
    await this.moveTo(locator);
    if (ctrl) await this.page.keyboard.down("Control");
    const frames = Math.max(1, Math.round(ms / FRAME_MS));
    let done = 0;
    for (let i = 1; i <= frames; i += 1) {
      // Eased, so a scroll gathers pace and coasts to a stop.
      const to = Math.round(deltaY * minJerk(i / frames));
      if (to !== done) await this.page.mouse.wheel(0, to - done);
      done = to;
      await this.wait(FRAME_MS);
    }
    if (ctrl) await this.page.keyboard.up("Control");
  }

  /** Type at a quick but human rate, a beat longer after punctuation. */
  async type(text, { cps = 32 } = {}) {
    for (const ch of text) {
      await this.page.keyboard.type(ch);
      const base = 1000 / cps;
      await this.wait(base * (0.55 + Math.random() * 0.9) + (/[,.?!]/.test(ch) ? 90 : 0));
    }
  }

  async press(key, label) {
    if (label) await this.stage("keys", [label]);
    await this.page.keyboard.press(key);
    if (label) {
      await this.wait(600);
      await this.stage("hideKeys");
    }
  }

  /** Tap Shift twice, the app's own shortcut for the assistant. */
  async doubleShift() {
    await this.stage("keys", ["⇧ Shift", "⇧ Shift"], 0);
    await this.page.keyboard.press("Shift");
    // The app wants the two taps within 400 ms of real time, slowed or not.
    await sleep(Math.min(150 * this.slow, 300));
    await this.page.keyboard.press("Shift");
    await this.wait(700);
    await this.stage("hideKeys");
  }

  /**
   * Select a phrase in the PDF by dragging across it.
   *
   * The drag is real, so the app paints the selection as it grows. A drag over
   * a text layer can land a character off at either end, so if the result is
   * not the phrase, the exact phrase is selected in its place and released.
   */
  async selectText(pageNo, phrase) {
    const points = await this.stage("textPoints", pageNo, phrase);
    if (!points) throw new Error(`"${phrase.slice(0, 40)}…" is not on page ${pageNo}`);
    await this.move(points.start.x, points.start.y);
    await this.wait(140);
    await this.page.mouse.down();
    const from = points.start;
    const to = points.end;
    // Down through the lines first, then along: the way a hand sweeps a
    // paragraph, and it keeps the drag inside the text the whole way.
    await this.trace((t) => {
      const e = minJerk(t);
      return { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * Math.min(1, e * 1.4) };
    }, 750);
    this.pos = { ...to };
    await this.page.mouse.up();
    await this.wait(120);
    const got = await this.stage("selectionText");
    const norm = (s) => s.replace(/\s+/g, "");
    if (norm(got) !== norm(phrase)) await this.stage("selectText", pageNo, phrase);
    await this.page.locator(".rd-popover").waitFor({ timeout: 5_000 });
  }

  /**
   * Wait for the assistant to finish, playing the wait fast.
   *
   * Real answers take real time: reading papers, calling tools, thinking. The
   * speed rises the longer it goes, and a badge says so, so the video stays
   * honest about it being sped up.
   */
  async whileAssistantWorks({ timeout = 240_000 } = {}) {
    const page = this.page;
    await page.locator(".as-thinking").waitFor({ timeout: 15_000 });
    const started = Date.now();
    const ladder = [[0, 3], [10_000, 6], [30_000, 12]];
    let speed = 0;
    for (;;) {
      const busy = await page.locator(".as-thinking").count();
      const failed = await page.locator(".as-error").count();
      if (!busy || failed) break;
      const waited = Date.now() - started;
      if (waited > timeout) throw new Error("The assistant did not finish in time.");
      const next = ladder.filter(([at]) => waited >= at).at(-1)[1];
      if (next !== speed) {
        speed = next;
        this.rec.setSpeed(speed);
        await this.stage("badge", `▶▶ ${speed}×`);
      }
      await sleep(100);
    }
    this.rec.setSpeed(1);
    await this.stage("badge", null);
    if (await page.locator(".as-error").count()) {
      throw new Error(`The assistant failed: ${await page.locator(".as-error").innerText()}`);
    }
  }

  /** Follow the app into a tab it opened, fading across. */
  async switchTo(page, ready) {
    await this.stage("cover", true, 380);
    await this.wait(420);
    this.rec.pause();
    this.page = page;
    await page.bringToFront();
    await page.waitForFunction(() => Boolean(window.__stage));
    await this.stage("cover", true, 0);
    await ready(page);
    await this.stage("fontsReady");
    await this.rec.attach(page);
    await sleep(150);
    this.rec.resume();
    await this.page.mouse.move(this.pos.x, this.pos.y);
    await this.stage("cover", false, 500);
    await this.wait(520);
  }
}

// --- main -------------------------------------------------------------------

async function main() {
  const fps = Number(opts.fps);
  const port = Number(opts.port);
  console.log(`Copying the library from ${opts.library}`);
  const home = await copyLibrary(opts.library);
  let server;
  let browser;
  try {
    server = await startServer(home, port);
    console.log(`Serving the copy on ${server.base}`);

    browser = await chromium.launch({
      channel: "chromium",
      headless: !opts.watch,
      // Wheel scrolling is done in small steps per frame here, already eased.
      // Chrome's own smoothing would run in real time and outpace slow motion.
      args: ["--disable-smooth-scrolling"],
    });
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
      colorScheme: "dark",
    });
    await context.addInitScript(`window.__stageSlow = ${SLOW};`);
    await context.addInitScript({ path: join(HERE, "stage.js") });
    const page = await context.newPage();

    const recorder = opts["no-video"] ? NO_RECORDER : new Recorder(resolve(opts.out), fps, SLOW);
    const director = new Director(page, recorder, SLOW);
    const script = await import(`./${opts.tour}.mjs`);

    await page.goto(server.base);
    await page.waitForSelector(".tl-box");
    await director.stage("fontsReady");
    // Whatever the first frame should show, set before recording starts.
    await script.setup?.(director);
    if (recorder !== NO_RECORDER) await recorder.start();
    await recorder.attach(page);

    if (SLOW > 1) console.log(`Recording at ${SLOW}x slow motion; this takes a while.`);
    await script.default(director, { base: server.base, context, out: resolve(opts.out) });

    if (recorder !== NO_RECORDER) {
      const { seconds, captured } = await recorder.stop();
      const rate = opts.out.endsWith(".gif") ? Number(opts["gif-fps"]) : fps;
      console.log(
        `Wrote ${opts.out}: ${seconds.toFixed(1)} s, ${VIDEO.width}x${VIDEO.height} at ${rate} fps ` +
        `(Chrome supplied ${captured.toFixed(0)} frames per second of video)`,
      );
    }
  } finally {
    await browser?.close().catch(() => undefined);
    server?.stop();
    await rm(home, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
