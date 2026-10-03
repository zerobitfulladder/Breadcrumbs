/**
 * The README's screenshots, taken from the live app at 2560x1440.
 *
 *   npm run stills   -> ../docs/grid.png, ../docs/reader.png
 *
 * The reader shot asks Bread for real, so it costs one answer.
 */
import { spawnSync } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { PAPER, card, zoomPdf } from "./tour.mjs";

const ASK =
  "Highlight the two or three sentences on the first two pages that carry the paper's argument, each with a one-line note.";

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

export default async function stills(d, { context, out }) {
  await mkdir(out, { recursive: true });
  let page = d.page;

  // The grid, with the pointer resting on one paper and its trail drawn. A
  // hover rather than a pin: pinning opens the side panel, which would cover
  // the recent columns the trail runs into.
  await d.hover(card(page, PAPER.title), 1200);
  await shot(d, join(out, "grid.png"));
  await d.click(card(page, PAPER.title), { after: 900 });

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
}
