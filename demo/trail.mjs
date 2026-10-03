/**
 * The citation trail, as a short loop for the README.
 *
 * Nothing is driven here. Left alone, the grid tours itself: after fifteen
 * seconds it lights a paper as though hovered, and another every three
 * seconds, drawing what each cites and what cites it. That is what gets
 * recorded, with no cursor in the way and nothing lit in passing.
 *
 *   npm run gif   -> ../docs/trail.gif
 *
 * Recorded at normal speed: the tour's timers run in real time, which slow
 * motion would only hurry.
 */

/**
 * The papers it lands on, in order: well connected, and alternating between
 * the old end of the grid and the new, so each one's lines sweep across it.
 *
 * The tour picks at random from the papers on screen. Its random numbers are
 * fed so that these are its picks; everything else, the lighting, the timing,
 * the drawing, is the app's own.
 */
const STOPS = [
  "Receptive fields, binocular interaction",
  "SoftHebb",
  "Emergence of simple-cell receptive field properties",
  "Self-organization of orientation sensitive cells",
  "Random synaptic feedback weights",
];

/**
 * Recording starts this long after the first paper lights, once its lines
 * have faded in (160 ms in Timeline.css). Started at the switch itself, the
 * loop would flash the undimmed grid at its seam on every pass.
 */
const SETTLE = 250;

let firstLitAt = 0;

const litTitle = (page) =>
  page.evaluate(() => document.querySelector(".tl-box.is-active .tl-box-title")?.textContent ?? null);

export async function setup(d) {
  const page = d.page;
  // The sparks that run along every line change every pixel they cross on
  // every frame, which doubles the size of the GIF. The lines, their fade-in
  // and the pulse on the lit card all stay; the video has the sparks.
  await page.addStyleTag({ content: ".tl-edge-spark { display: none; }" });
  await d.stage("hidden", true);
  await page.evaluate((titles) => {
    // The tour draws from the cards on screen, in the order the grid lays
    // them out, which is their order in the document. On screen means what
    // Timeline.tsx counts: overlapping the canvas, with an 8 px margin.
    const canvas = document.querySelector(".tl-canvas").getBoundingClientRect();
    const onScreen = [...document.querySelectorAll(".tl-box")].filter((el) => {
      const r = el.getBoundingClientRect();
      const left = r.left - canvas.left;
      const top = r.top - canvas.top;
      return left + r.width > 8 && left < canvas.width - 8 && top + r.height > 8 && top < canvas.height - 8;
    });
    const picks = titles.map((t) => {
      const i = onScreen.findIndex((el) => el.querySelector(".tl-box-title").textContent.startsWith(t));
      if (i < 0) throw new Error(`Not on screen: ${t}`);
      return (i + 0.5) / onScreen.length;
    });
    const random = Math.random;
    Math.random = () => (picks.length ? picks.shift() : random());
  }, STOPS);
  // No input from here on, so the grid's idle wait runs out and it starts on
  // its own. Recording begins with the first paper it lights.
  await page.waitForFunction(() => document.querySelector(".tl-box.is-active"), null, {
    timeout: 40_000, polling: "raf",
  });
  firstLitAt = Date.now();
  await new Promise((r) => setTimeout(r, SETTLE));
}

export default async function trail(d) {
  const page = d.page;
  let current = await litTitle(page);
  console.log(`  ${current}`);
  for (let i = 1; i < STOPS.length; i += 1) {
    await page.waitForFunction(
      (prev) => (document.querySelector(".tl-box.is-active .tl-box-title")?.textContent ?? null) !== prev,
      current, { timeout: 10_000, polling: "raf" },
    );
    current = await litTitle(page);
    console.log(`  ${current}`);
  }
  // Exactly one step per paper, so the seam where the loop starts over falls
  // where the tour's next step would, and reads as one.
  const step = (Date.now() - firstLitAt) / (STOPS.length - 1);
  const end = firstLitAt + SETTLE + STOPS.length * step - 40;
  await new Promise((r) => setTimeout(r, Math.max(0, end - Date.now())));
}
