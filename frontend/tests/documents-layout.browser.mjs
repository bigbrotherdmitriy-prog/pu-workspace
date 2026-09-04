// Run from frontend: node tests/documents-layout.browser.mjs
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
const output = mkdtempSync(join(tmpdir(), "pu-documents-layout-"));
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL("../", import.meta.url)),
  plugins: [react()],
  server: { host: "127.0.0.1", port: 0 },
});
let browser;
const failures = [];
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  });

  for (const width of [390, 768, 1024, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    try {
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("**/*", (route) => new URL(route.request().url()).origin === origin
        ? route.continue()
        : route.abort());
      await page.goto(`${origin}/tests/fixtures/documents-layout.html`);
      await page.locator(".document-register > button").first().waitFor();

      assert.equal(await page.locator(".document-register > button").count(), 462, `${width}: fixture count`);
      await page.getByPlaceholder("Поиск по названию или сводке").fill("Уникальный план-график № 321");
      assert.equal(await page.locator(".document-register > button").count(), 1, `${width}: search result count`);
      assert.match(await page.locator(".document-register > button strong").textContent(), /321/);
      await page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click();

      await page.getByRole("button", { name: /^Требуют внимания/ }).click();
      const attentionRows = page.locator(".document-register > button");
      assert.ok(await attentionRows.count() > 0, `${width}: quick filter has results`);
      assert.equal(
        await attentionRows.locator(".document-status:not(.attention)").count(),
        0,
        `${width}: quick filter only shows attention documents`,
      );

      const layout = await page.evaluate(() => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      };
      const rect = (selector) => document.querySelector(selector)?.getBoundingClientRect();
      const intersects = (a, b) => Boolean(a && b && a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1);
      const horizontalOverflow = [
        ".documents-overlay",
        ".documents-layout",
        ".document-register-panel",
        ".document-register-tools",
        ".document-filter-row",
        ".document-detail",
      ].filter((selector) => {
        const element = document.querySelector(selector);
        return element && element.clientWidth > 0 && element.scrollWidth > element.clientWidth + 1;
      });
      const overlay = rect(".documents-overlay");
      const panelA = rect(".document-register-panel");
      const panelB = rect(".document-detail");
      const headParts = [...document.querySelectorAll(".document-register-head > *")]
        .filter(visible).map((element) => element.getBoundingClientRect());
      const filterParts = [...document.querySelectorAll(".document-filter-row > label")]
        .filter(visible).map((element) => element.getBoundingClientRect());
      const siblingCollision = (items) => items.some((a, index) => items.slice(index + 1).some((b) => intersects(a, b)));
      const viewportEscape = [...document.querySelectorAll([
        ".documents-overlay",
        ".documents-layout",
        ".document-register-panel",
        ".document-register-tools",
        ".document-filter-row",
        ".document-register",
        ".document-detail",
      ].join(","))]
        .filter(visible)
        .filter((element) => {
          const bounds = element.getBoundingClientRect();
          return bounds.left < -1 || bounds.right > window.innerWidth + 1;
        }).map((element) => element.className || element.tagName);
      return {
        pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        horizontalOverflow,
        viewportEscape,
        overlayWidth: overlay?.width || 0,
        registerWidth: panelA?.width || 0,
        detailWidth: panelB?.width || 0,
        panelCollision: intersects(panelA, panelB),
        headCollision: siblingCollision(headParts),
        filterCollision: siblingCollision(filterParts),
      };
      });

      assert.equal(layout.pageOverflow, false, `${width}: page horizontal overflow`);
      assert.deepEqual(layout.horizontalOverflow, [], `${width}: horizontally scrolling containers`);
      assert.deepEqual(layout.viewportEscape, [], `${width}: content outside viewport`);
      assert.equal(layout.panelCollision, false, `${width}: registry/detail overlap`);
      assert.equal(layout.headCollision, false, `${width}: registry header overlap`);
      assert.equal(layout.filterCollision, false, `${width}: filter controls overlap`);
      if (width <= 760) {
        assert.ok(layout.overlayWidth >= width - 2, `${width}: mobile overlay is only ${layout.overlayWidth}px wide`);
        assert.ok(layout.registerWidth >= width - 40, `${width}: registry squeezed to ${layout.registerWidth}px`);
        assert.ok(layout.detailWidth >= width - 40, `${width}: detail squeezed to ${layout.detailWidth}px`);
      } else {
        assert.ok(layout.registerWidth >= 320, `${width}: registry squeezed to ${layout.registerWidth}px`);
        assert.ok(layout.detailWidth >= 280, `${width}: detail squeezed to ${layout.detailWidth}px`);
      }
      assert.deepEqual(errors, [], `${width}: browser errors`);
      console.log(`PASS ${width}px: 462 documents; search/filter; no horizontal overflow/overlap`);
    } catch (error) {
      failures.push(`${width}px: ${error.message}`);
      console.error(`FAIL ${width}px: ${error.message}`);
    } finally {
      await page.screenshot({ path: join(output, `${width}-attention.png`), fullPage: true });
      await page.close();
    }
  }
  assert.deepEqual(failures, [], "Documents layout regressions");
} finally {
  await browser?.close();
  await server.close();
  console.log(`Screenshots: ${output}`);
}
