// Run from frontend: node tests/project-site-location.browser.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const { chromium } = createRequire(import.meta.url)("playwright");
const output = mkdtempSync(join(tmpdir(), "pu-site-location-"));
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL("../", import.meta.url)),
  plugins: [react()],
  server: { host: "127.0.0.1", port: 0 },
});
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  for (const width of [390, 768, 1280]) {
    const page = await browser.newPage({ viewport: { width, height: 760 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/tests/fixtures/project-site-location.html`);
    await page.getByText("Стройплощадка · корпус 2").waitFor();
    await page.getByRole("button", { name: /проверить.*местоположение/i }).click();
    await page.getByText(/Вы на объекте/).waitFor();
    const layout = await page.evaluate(() => {
      const card = document.querySelector(".site-location-card");
      const buttons = [...document.querySelectorAll(".site-location-actions button")];
      return {
        pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        cardOverflow: card ? card.scrollWidth > card.clientWidth + 1 : true,
        shortTargets: buttons.filter((button) => button.getBoundingClientRect().height < 44).length,
      };
    });
    assert.deepEqual(errors, [], `${width}: browser errors`);
    assert.equal(layout.pageOverflow, false, `${width}: page overflow`);
    assert.equal(layout.cardOverflow, false, `${width}: card overflow`);
    assert.equal(layout.shortTargets, 0, `${width}: touch targets under 44px`);
    await page.screenshot({ path: join(output, `${width}.png`), fullPage: true });
    await page.close();
    console.log(`PASS ${width}px: GPS card, local distance, no overflow`);
  }
} finally {
  await browser?.close();
  await server.close();
  console.log(`Screenshots: ${output}`);
}
