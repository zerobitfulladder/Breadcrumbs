/**
 * The tour: what the video shows, scene by scene.
 *
 * Each scene is real input to the real app. The assistant answers live, so no
 * two recordings say quite the same thing; the waits for it are played fast,
 * with a badge saying how fast.
 */

/** The paper the tour opens and reads. Its PDF needs a text layer. */
export const PAPER = {
  id: 90,
  title: "Random synaptic feedback weights support error backpropagation",
  author: "Timothy Lillicrap",
};

/** Papers lit on the way to it, to show the trail before following one. */
const ON_THE_WAY = [
  "Gradient-based learning applied to document recognition",
  "A Fast Learning Algorithm for Deep Belief Nets",
];

/** Passages on the first page of the PDF, quoted as they appear. */
const TO_HIGHLIGHT =
  "We present a surprisingly simple mechanism that assigns blame by multiplying errors by even random synaptic weights.";
const TO_ASK_ABOUT =
  "However, this involves a precise, symmetric backward connectivity pattern, which is thought to be impossible in the brain.";

const ASK_LIBRARY = "Which papers in my library build on this one? Three short bullets.";
const ASK_PASSAGE = "Why would that be impossible in the brain? Two or three sentences.";
const ASK_TO_MARK =
  "Highlight the sentence where they name the weight transport problem, with a one-line note on who named it.";

export const card = (page, title) =>
  page.locator(".tl-box", { has: page.locator(".tl-box-title", { hasText: title }) });

/** Time to read an answer: a beat plus the words, within reason. */
async function readingTime(page) {
  const text = await page.locator(".as-turn.is-assistant").last().innerText().catch(() => "");
  const words = text.split(/\s+/).filter(Boolean).length;
  return Math.min(5000, 1600 + words * 45);
}

/** The rectangle around several boxes, for framing a selection with its menu. */
function union(...boxes) {
  const x = Math.min(...boxes.map((b) => b.x));
  const y = Math.min(...boxes.map((b) => b.y));
  const r = Math.max(...boxes.map((b) => b.x + b.w));
  const b = Math.max(...boxes.map((b) => b.y + b.h));
  return { x, y, w: r - x, h: b - y };
}

/** The top `h` pixels of a box: the part of a full-height panel worth framing. */
async function topOf(d, locator, h) {
  const b = await d.box(locator);
  return { ...b, h: Math.min(b.h, h) };
}

/**
 * Frame the latest question and its answer in a chat, after scrolling the
 * question to the top of the log. A tall answer is framed from its start.
 */
async function frameAnswer(d, chat, opts) {
  await d.stage("scrollToChild", `${chat} .as-log`, ".as-turn.is-user");
  await d.wait(600);
  const asked = await d.box(d.page.locator(`${chat} .as-turn.is-user`).last());
  const answer = await d.box(d.page.locator(`${chat} .as-turn.is-assistant`).last());
  const log = await d.box(d.page.locator(`${chat} .as-log`));
  const bottom = Math.min(answer.y + answer.h, log.y + log.h);
  await d.focus({ x: log.x, y: asked.y - 6, w: log.w, h: bottom - asked.y + 12 }, opts);
}

/** Zoom the PDF with ctrl and the wheel, the app's own gesture, to a given percentage. */
export async function zoomPdf(d, percent) {
  const page = d.page;
  await d.moveTo(page.locator(".pv-scroll"), { dx: 0.3, dy: 0.5 });
  await page.keyboard.down("Control");
  // A notch a frame, checking the app's own readout, since a notch can be
  // swallowed while the pages relay out.
  for (let i = 0; i < 90; i += 1) {
    if (parseInt(await page.locator(".pv-zoom").innerText(), 10) >= percent) break;
    await page.mouse.wheel(0, -70);
    await d.wait(17);
  }
  await page.keyboard.up("Control");
}

/** Scroll the PDF with the wheel until a phrase sits in the upper part of the view. */
async function bringIntoView(d, pageNo, phrase) {
  const pdf = d.page.locator(".pv-scroll");
  for (let tries = 0; tries < 6; tries += 1) {
    const pts = await d.stage("textPoints", pageNo, phrase);
    const view = await d.box(pdf);
    if (!pts) throw new Error(`"${phrase.slice(0, 40)}…" is not on page ${pageNo}`);
    const top = pts.box.y - view.y;
    if (top > 60 && pts.box.y + pts.box.h < view.y + view.h * 0.6) return;
    await d.wheel(pdf, top - view.h * 0.3, { ms: 550 });
    await d.wait(250);
  }
}

/** The first frame: dark, for the title card to lift off. */
export async function setup(d) {
  await d.stage("cover", true, 0);
}

export default async function tour(d, { context }) {
  let page = d.page;

  // --- Open on the name -----------------------------------------------------
  await d.move(720, 420, { ms: 1 });
  await d.stage("card", "Breadcrumbs", "Follow the trail of ideas through your papers.");
  await d.wait(400);
  await d.stage("cover", false, 1400);
  await d.wait(2600);
  await d.stage("card", null);
  await d.wait(600);

  // --- The grid -------------------------------------------------------------
  await d.caption("Your whole library, laid out in time", "Every paper on one grid, a column for each five years");
  await d.move(760, 380);
  await d.focus(".tl-canvas", { scale: 1.1, ms: 3000 });

  await d.caption("Hover a paper to follow its trail", "Magenta for what it cites, cyan for what cites it back");
  for (const title of ON_THE_WAY) await d.hover(card(page, title), 1000);
  await d.hover(card(page, PAPER.title), 800);
  await d.click(card(page, PAPER.title), { after: 600 });
  await d.reset(900);

  // --- The paper ------------------------------------------------------------
  await d.caption("Every paper at a glance", "Status, authors, identifiers, your notes and its full reference list");
  await d.focus(await topOf(d, page.locator(".app-detail"), 330), { fill: 0.9, max: 1.7 });
  await d.wait(1000);
  await d.wheel(page.locator(".app-detail"), 1100, { ms: 900 });
  await d.wait(1300);
  await d.wheel(page.locator(".app-detail"), -1100, { ms: 600 });
  await d.wait(200);

  // --- An author ------------------------------------------------------------
  const author = page.locator(".app-authors .ac-name", { hasText: PAPER.author });
  await d.hover(author, 1000);
  await d.click(author, { after: 800 });
  await d.caption("And the people behind it", "Portraits and biographies, identity checked through Wikidata");
  await d.focus(await topOf(d, page.locator(".ap-root"), 420), { fill: 0.9, max: 1.7 });
  await d.wait(2400);
  await d.click(page.locator(".ap-back"), { after: 500 });
  await d.reset(900);

  // --- The assistant, on the library ----------------------------------------
  await d.uncaption();
  await d.move(700, 330);
  await d.doubleShift();
  await d.caption("Meet Bread, your research assistant", "Tap Shift twice, anywhere");
  await d.focus(".as-root", { fill: 0.96, max: 1.4 });
  await d.click(page.locator(".as-compose textarea"), { after: 200 });
  await d.type(ASK_LIBRARY);
  await d.press("Enter");
  await d.caption("It reads your library, and shows its work", "Every step streams in as it happens");
  await d.whileAssistantWorks();
  await frameAnswer(d, ".as-root", { fill: 0.9, max: 1.6 });
  await d.wait(await readingTime(page));
  await d.uncaption();
  await d.reset(800);
  await d.doubleShift();

  // --- The reader -----------------------------------------------------------
  const opened = context.waitForEvent("page");
  await d.click(page.locator(".app-open-reader"), { after: 100 });
  page = await opened;
  await d.switchTo(page, async (p) => {
    await p.locator('.pv-page[data-page="1"] .textLayer span').first().waitFor({ timeout: 30_000 });
    await p.locator('.pv-page[data-page="1"] canvas').first().waitFor();
    await p.waitForTimeout(1200);
  });
  await d.caption("Read with everything beside you", "The PDF on one side, your notes and Bread on the other");
  await d.wait(900);
  await zoomPdf(d, 90);
  await d.wait(1200);

  // --- Highlighting ---------------------------------------------------------
  await bringIntoView(d, 1, TO_HIGHLIGHT);
  await d.caption("Highlight as you read", "Select a passage and pick a pen");
  await d.selectText(1, TO_HIGHLIGHT);
  const passage = (await d.stage("textPoints", 1, TO_HIGHLIGHT)).box;
  await d.focus(union(passage, await d.box(page.locator(".rd-popover"))), { fill: 0.8, max: 2 });
  await d.wait(400);
  await d.click(page.locator(".rd-popover .rd-color").first(), { after: 1000 });
  await d.reset(900);

  // --- Asking about a passage -----------------------------------------------
  await bringIntoView(d, 1, TO_ASK_ABOUT);
  await d.caption("Ask about any passage", "Hand Bread the words you are reading");
  await d.selectText(1, TO_ASK_ABOUT);
  await d.wait(300);
  await d.click(page.locator(".rd-popover button", { hasText: "Add to context" }), { after: 700 });
  // A fresh conversation, rather than carrying on the one from the library.
  await d.click(page.locator(".rd-chat .as-tab-new"), { after: 500 });
  await d.click(page.locator(".rd-chat .as-compose textarea"), { after: 200 });
  await d.type(ASK_PASSAGE);
  await d.press("Enter");
  await d.whileAssistantWorks();
  await frameAnswer(d, ".rd-chat", { fill: 0.88, max: 1.7 });
  await d.wait(await readingTime(page));
  await d.reset(900);

  // --- The assistant marking the page ---------------------------------------
  await d.caption("Bread can mark the page too", "Its highlights and notes appear while it works");
  await d.click(page.locator(".rd-chat .as-compose textarea"), { after: 200 });
  await d.type(ASK_TO_MARK);
  await d.press("Enter");
  await d.whileAssistantWorks();
  await d.wait(900);
  // Bread's mark is the one carrying a note; it draws one box per line.
  // Failing that, its marks are the ones off the first page, where ours is.
  const noted = page.locator(".pv-mark.has-note");
  const later = page.locator('.pv-page:not([data-page="1"]) .pv-mark');
  const marks = (await noted.count()) ? noted : (await later.count()) ? later : page.locator(".pv-mark");
  const lines = [];
  for (const m of await marks.all()) lines.push(await d.box(m));
  await d.hover(marks.first(), 700);
  await d.focus(union(...lines), { fill: 0.55, max: 2 });
  await d.wait(2600);
  await d.reset(900);

  // --- Everything in one place ----------------------------------------------
  await d.click(page.locator(".rd-tabs button", { hasText: "Notes" }), { after: 500 });
  await d.caption("Everything you mark, in one place", "Yours and Bread's, each a click from its passage");
  await d.stage("scrollToChild", ".rd-notes-scroll", ".rd-highlights");
  await d.wait(700);
  await d.focus(".rd-highlights", { fill: 0.85, max: 1.6 });
  await d.wait(900);
  await d.click(page.locator(".rd-highlights li").first(), { after: 1200 });
  await d.reset(900);

  // --- Close ----------------------------------------------------------------
  await d.uncaption();
  await d.stage("card", "Breadcrumbs", "Local-first. <b>Your library stays on your machine.</b>");
  await d.wait(3800);
  await d.stage("cover", true, 900);
  await d.wait(1000);
}
