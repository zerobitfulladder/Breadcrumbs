/**
 * The README's screenshots, taken from the live app at 2560x1440.
 *
 *   npm run stills   -> ../docs/grid.png, ../docs/reader.png, ../docs/grounding.png,
 *                       ../docs/three-kinds.png, ../docs/proposal.png, and one
 *                       ../docs/answer-*.png per kind of answer, plus one
 *                       declining a question the paper cannot answer
 *
 * Bread answers for real, six times, so a run costs six answers.
 */
import { spawnSync } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  ASK_ABSENT, ASK_FACT, ASK_KNOWLEDGE, ASK_LIBRARY, PAPER, card, zoomPdf,
} from "./tour.mjs";

/** The three kinds of statement, in the order the README explains them. */
const KINDS = [
  { key: "paper", colour: "#ccff00", title: "What the paper says",
    sub: "Quoted, and every quote found word for word in the PDF" },
  { key: "library", colour: "#05d9e8", title: "What your library says",
    sub: "Read from its records through tools, no quote needed" },
  { key: "knowledge", colour: "#a0a8c8", title: "What Bread knows",
    sub: "Its own explanation, welcome, and labelled as its own" },
];

const ASK =
  "Highlight the two or three sentences on the first two pages that carry the paper's argument, each with a one-line note.";
/**
 * A paper the library lacks, which Bread can only propose. Named, so the card
 * is the same from run to run, and a journal article, which every source
 * holds: an arXiv-only paper leaves three of the four source panels empty.
 */
const ASK_SUGGEST =
  "I'd like to read Guerguiev, Lillicrap and Richards' 2017 eLife paper on segregated dendrites. Give it to me as a paper link, with one line on how it builds on feedback alignment.";

/** The app alone, without the stage, scaled down from the 2x render. */
async function shot(d, path) {
  await d.stage("hidden", true);
  await d.page.waitForTimeout(400);
  const raw = `${path}.raw.png`;
  await d.page.screenshot({ path: raw });
  const scaled = spawnSync("ffmpeg", [
    "-y", "-loglevel", "error", "-i", raw,
    "-vf", "scale=2560:-1:flags=lanczos", "-compression_level", "9", path,
  ], { stdio: "inherit" });
  await rm(raw, { force: true });
  if (scaled.status !== 0) throw new Error(`Could not write ${path}`);
  await d.stage("hidden", false);
  console.log(`  wrote ${path}`);
}

/** Ask in a chat panel and wait for the whole answer. */
async function ask(d, chat, question) {
  await d.click(d.page.locator(`${chat} .as-compose textarea`), { after: 200 });
  await d.type(question);
  await d.press("Enter");
  await d.whileAssistantWorks();
  await d.wait(800);
}

/**
 * The latest question and its answer in a chat panel, as a PNG of that
 * region, also written to `path` as an image of its own.
 */
async function answerClip(d, chat, path) {
  const page = d.page;
  const measure = async () => {
    await d.stage("scrollToChild", `${chat} .as-log`, ".as-turn.is-user");
    await page.waitForTimeout(700);
    return {
      asked: await d.box(page.locator(`${chat} .as-turn.is-user`).last()),
      answer: await d.box(page.locator(`${chat} .as-turn.is-assistant`).last()),
      log: await d.box(page.locator(`${chat} .as-log`)),
    };
  };
  let { asked, answer, log } = await measure();
  // A long answer runs past the panel's bottom edge, so the window is made
  // taller for this shot alone, by exactly what is missing.
  const size = page.viewportSize();
  const missing = answer.y + answer.h + 10 - (log.y + log.h);
  if (missing > 0) {
    await page.setViewportSize({ width: size.width, height: size.height + Math.ceil(missing) + 40 });
    ({ asked, answer, log } = await measure());
  }
  const top = asked.y - 10;
  const bottom = Math.min(answer.y + answer.h + 10, log.y + log.h);
  await d.stage("hidden", true);
  await page.waitForTimeout(200);
  const png = await page.screenshot({ clip: { x: log.x, y: top, width: log.w, height: bottom - top } });
  await d.stage("hidden", false);
  if (missing > 0) await page.setViewportSize(size);
  await writeFile(path, png);
  console.log(`  wrote ${path}`);
  return { png: png.toString("base64"), width: log.w };
}

/**
 * The three answers side by side under their headings, laid out as a page in
 * the same browser and screenshotted, then scaled like the other stills.
 */
async function compose(context, clips, path) {
  const page = await context.newPage();
  const figures = KINDS.map((k) => `
    <figure style="width:${clips[k.key].width}px">
      <div class="dot" style="background:${k.colour}"></div>
      <h2 style="color:${k.colour}">${k.title}</h2>
      <p>${k.sub}</p>
      <img src="data:image/png;base64,${clips[k.key].png}" style="width:${clips[k.key].width}px">
    </figure>`).join("");
  await page.setContent(`<!doctype html><html><head><style>
    body { margin: 0; background: #0a0418; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .row { display: flex; gap: 36px; padding: 40px 48px 48px; align-items: flex-start; width: max-content; }
    figure { margin: 0; }
    .dot { width: 26px; height: 3px; margin-bottom: 12px; }
    h2 { margin: 0 0 6px; font-size: 21px; font-weight: 600; }
    p { margin: 0 0 16px; font-size: 14px; color: #a0a8c8; }
    img { display: block; border: 1px solid #5a4a78; background: #0d0820; }
  </style></head><body><div class="row">${figures}</div></body></html>`);
  await page.waitForTimeout(400);
  const raw = `${path}.raw.png`;
  await writeFile(raw, await page.locator(".row").screenshot());
  await page.close();
  const scaled = spawnSync("ffmpeg", [
    "-y", "-loglevel", "error", "-i", raw,
    "-vf", "scale=2560:-1:flags=lanczos", "-compression_level", "9", path,
  ], { stdio: "inherit" });
  await rm(raw, { force: true });
  if (scaled.status !== 0) throw new Error(`Could not write ${path}`);
  console.log(`  wrote ${path}`);
}

export default async function stills(d, { context, out }) {
  await mkdir(out, { recursive: true });
  let page = d.page;

  // The grid, with the pointer resting on one paper and its trail drawn. A
  // hover rather than a pin: pinning opens the side panel, which would cover
  // the recent columns the trail runs into.
  await d.hover(card(page, PAPER.title), 1200);
  await shot(d, join(out, "grid.png"));
  await d.click(card(page, PAPER.title), { after: 900 });

  // Two of the three kinds, asked from the library with no paper open: one
  // answered from the library's records, one from the model's own knowledge.
  const clips = {};
  await d.move(700, 330);
  await d.doubleShift();
  await ask(d, ".as-root", ASK_LIBRARY);
  clips.library = await answerClip(d, ".as-root", join(out, "answer-library.png"));
  await ask(d, ".as-root", ASK_KNOWLEDGE);
  clips.knowledge = await answerClip(d, ".as-root", join(out, "answer-knowledge.png"));

  // A paper Bread can only propose: the preview card, fetched live from the
  // bibliographic sources, with nothing saved until you choose to add it.
  await ask(d, ".as-root", ASK_SUGGEST);
  const proposal = page.locator(".as-root .md-paper").first();
  if (await proposal.count()) {
    await d.click(proposal, { after: 400 });
    // Finished: the save button no longer says Fetching, and the reference
    // list has its count.
    await page.waitForFunction(() => {
      const btn = document.querySelector(".ppc-card button.primary");
      const refs = document.querySelector(".ppc-card .add-tabs button.is-active");
      return /Add to library|Update this paper/.test(btn?.textContent ?? "")
        && /\(\d+\)/.test(refs?.textContent ?? "");
    }, null, { timeout: 120_000 }).catch(() => undefined);
    // Semantic Scholar throttles without a key; its panel has a Retry, used
    // here as a person would, a few times with a pause between.
    for (let attempt = 1; attempt <= 4; attempt++) {
      const failed = page.locator(".ppc-card .add-source.status-error");
      if (!(await failed.count())) break;
      await page.waitForTimeout(4000 * attempt);
      await failed.first().locator("button.retry").click();
      await page.locator(".ppc-card button.retry", { hasText: "Retrying…" })
        .waitFor({ state: "detached", timeout: 60_000 }).catch(() => undefined);
    }
    // The card alone, from its sources through the abstract. It scrolls in a
    // window this size, so it is photographed in a taller one; the reference
    // list (its count is in the stats) is left out, and the abstract, which
    // scrolls in its own box, is shown whole.
    const size = page.viewportSize();
    await page.setViewportSize({ width: size.width, height: 2200 });
    const style = await page.addStyleTag({
      content: ".ppc-card section:has(.add-tabs) { display: none; } "
        + ".ppc-card .add-abstract { max-height: none; overflow: visible; }",
    });
    await d.stage("hidden", true);
    await page.waitForTimeout(700);
    const path = join(out, "proposal.png");
    await writeFile(path, await page.locator(".ppc-card").screenshot());
    console.log(`  wrote ${path}`);
    await d.stage("hidden", false);
    await style.evaluate((el) => el.remove());
    await page.setViewportSize(size);
    await page.waitForTimeout(400);
    await d.click(page.locator(".ppc-close"), { after: 400 });
  }
  await d.doubleShift();

  // The reader, with Bread marking passages beside the conversation.
  const opened = context.waitForEvent("page");
  await d.click(page.locator(".app-open-reader"), { after: 100 });
  page = await opened;
  await d.switchTo(page, async (p) => {
    await p.locator('.pv-page[data-page="1"] .textLayer span').first().waitFor({ timeout: 30_000 });
    await p.waitForTimeout(1200);
  });
  await zoomPdf(d, 90);
  await d.click(page.locator(".rd-tabs button", { hasText: "Bread" }), { after: 600 });
  // A conversation of its own: the reader would otherwise reopen the last one,
  // the library questions above.
  await d.click(page.locator(".rd-chat .as-tab-new"), { after: 500 });
  await d.click(page.locator(".rd-chat .as-compose textarea"));
  await d.type(ASK);
  await d.press("Enter");
  await d.whileAssistantWorks();
  await d.wait(1500);
  // Rest on one of Bread's marks in view, so its note shows.
  const inView = await page.evaluate(() => {
    const view = document.querySelector(".pv-scroll").getBoundingClientRect();
    const marks = [...document.querySelectorAll(".pv-mark.has-note")];
    return marks.findIndex((m) => {
      const r = m.getBoundingClientRect();
      return r.top > view.top + 80 && r.bottom < view.bottom - 40;
    });
  });
  if (inView >= 0) await d.hover(page.locator(".pv-mark.has-note").nth(inView), 900);
  await shot(d, join(out, "reader.png"));

  // A question about what the paper says: the answer with its checked quotes
  // and its record open, and the PDF showing the passage its chip points to.
  await d.click(page.locator(".rd-chat .as-tab-new"), { after: 500 });
  await d.click(page.locator(".rd-chat .as-compose textarea"));
  await d.type(ASK_FACT);
  await d.press("Enter");
  await d.whileAssistantWorks();
  await d.wait(800);
  // The third kind, before the record is opened, so the clip stays an answer.
  clips.paper = await answerClip(d, ".rd-chat", join(out, "answer-paper.png"));
  const toggle = page.locator(".rd-chat .ar-toggle").last();
  if (await toggle.count()) await d.click(toggle, { after: 500 });
  await d.stage("scrollToChild", ".rd-chat .as-log", ".as-turn.is-user");
  await d.wait(600);
  const chip = page.locator(".rd-chat .md-cite.is-ok").last();
  if (await chip.count()) {
    await d.hover(chip, 300);
    await d.click(chip, { after: 200 });
    // The flash on the passage holds for about a second and a half.
    await page.locator(".pv-reveal").first().waitFor({ timeout: 4000 }).catch(() => undefined);
    await d.wait(250);
  }
  await shot(d, join(out, "grounding.png"));
  await compose(context, clips, join(out, "three-kinds.png"));

  // A question the paper cannot answer, in the same conversation.
  if (await toggle.count()) await d.click(toggle, { after: 300 });
  await ask(d, ".rd-chat", ASK_ABSENT);
  await answerClip(d, ".rd-chat", join(out, "answer-not-in-paper.png"));
}
