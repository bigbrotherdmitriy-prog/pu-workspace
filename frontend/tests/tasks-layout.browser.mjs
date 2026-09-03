// Run from frontend: node tests/tasks-layout.browser.mjs
// Requires Playwright + Chromium (or PLAYWRIGHT_CHANNEL=msedge/chrome), not production.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const { chromium } = createRequire(import.meta.url)("playwright");
const output = mkdtempSync(join(tmpdir(), "pu-task-layout-"));
const server = await createServer({
  configFile: false, root: fileURLToPath(new URL("../", import.meta.url)),
  plugins: [react()], server: { host: "127.0.0.1", port: 0 },
});
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  for (const width of [390, 768, 1024, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/*", (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(`${origin}/tests/fixtures/tasks-layout.html`);
    await page.locator(".task-body strong").waitFor();
    for (const expanded of [false, true]) {
      if (expanded) {
        await page.getByRole("button", { name: "Завершить", exact: true }).click();
        await page.getByRole("button", { name: "История", exact: true }).click();
        await page.locator(".task-completion textarea").fill("Синтетическое подтверждение выполнения");
        await page.locator(".task-completion select").selectOption("7");
        await page.waitForFunction(() => !document.querySelector(".task-completion-actions .complete").disabled);
      }
      const result = await page.evaluate(() => {
        const bounds = (selector) => document.querySelector(selector).getBoundingClientRect();
        const article = bounds(".task-list article");
        const body = bounds(".task-body");
        const meta = bounds(".task-meta");
        const actions = bounds(".task-actions");
        const intersects = (a, b) => a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1;
        const panels = [...document.querySelectorAll(".task-body, .task-meta, .task-actions, .task-completion, .task-history")].map((el) => el.getBoundingClientRect());
        const controls = [...document.querySelectorAll(".task-assignee, .task-action-buttons button")].map((el) => el.getBoundingClientRect());
        const overlaps = (items) => items.some((a, i) => items.slice(i + 1).some((b) => intersects(a, b)));
        const overflow = [...document.querySelectorAll(".task-register *")].filter((el) => {
          if (el.tagName === "OPTION") return false;
          const r = el.getBoundingClientRect();
          return r.width > 0 && (r.right > article.right + 4 || r.left < article.left - 4 ||
            (!["SELECT", "TEXTAREA"].includes(el.tagName) && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1));
        }).map((el) => el.className || el.tagName);
        return { bodyWidth: body.width, availableWidth: article.width, overflow,
          pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
          collision: overlaps(panels) || overlaps(controls) };
      });
      await page.screenshot({ path: join(output, `${width}-${expanded ? "expanded" : "closed"}.png`), fullPage: true });
      assert.equal(result.pageOverflow, false, `${width}: page overflow`);
      assert.deepEqual(result.overflow, [], `${width}: overflowing elements`);
      assert.equal(result.collision, false, `${width}: body/meta/actions overlap`);
      assert.ok(result.bodyWidth >= result.availableWidth * 0.8, `${width}: body squeezed to ${result.bodyWidth}px`);
      assert.deepEqual(errors, [], `${width}: browser errors`);
      console.log(`PASS ${width}px ${expanded ? "expanded" : "closed"}: text ${Math.round(result.bodyWidth)}px; no overflow/overlap`);
    }
    await page.close();
  }
} finally {
  await browser?.close();
  await server.close();
  console.log(`Screenshots: ${output}`);
}
