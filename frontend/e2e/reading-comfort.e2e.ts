import { expect, test } from "./storage-fixtures";

test("reading themes retain contrast, legible copy and responsive layout", async ({ page, mock }, info) => {
  mock.projectRows[1].name = "Реконструкция инженерного корпуса";
  await page.route("**/projects/2/site-location", route => route.fulfill({ json: {
    configured: false, latitude: null, longitude: null, label: null, radius_m: null, accuracy_m: null,
  } }));
  await page.goto("/new/");
  await expect(page.locator(".dashboard-overview-deck")).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page.getByRole("button", { name: theme === "light" ? "Светлая" : "Тёмная", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-display-theme", theme);
    const metrics = await page.evaluate(() => {
      const style = getComputedStyle(document.querySelector(".dashboard-hero-copy > p")!);
      const surface = getComputedStyle(document.querySelector(".dashboard-hero")!);
      function luminance(value: string) {
        const channels = value.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(n => {
          const c = n / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
        });
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      }
      const text = luminance(style.color), bg = luminance(surface.backgroundColor);
      return { contrast: (Math.max(text, bg) + .05) / (Math.min(text, bg) + .05),
        font: parseFloat(style.fontSize), lineHeight: parseFloat(style.lineHeight) / parseFloat(style.fontSize),
        overflow: document.documentElement.scrollWidth > innerWidth,
        heroHeight: document.querySelector(".dashboard-hero")!.getBoundingClientRect().height };
    });
    expect(metrics.contrast).toBeGreaterThanOrEqual(7);
    expect(metrics.contrast).toBeLessThanOrEqual(10);
    expect(metrics.font).toBeGreaterThanOrEqual(15);
    expect(metrics.lineHeight).toBeGreaterThanOrEqual(1.5);
    expect(metrics.overflow).toBe(false);
    expect(metrics.heroHeight).toBeLessThan(340);
    await page.screenshot({ path: info.outputPath(`comfort-${theme}.png`), fullPage: true });
  }
  await page.getByRole("button", { name: "Режим комфорта" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-comfort", "true");
  await page.reload();
  await expect(page.getByRole("button", { name: "Режим комфорта" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).toHaveAttribute("data-display-theme", "dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("group", { name: "Тема интерфейса" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("comfort-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Открыть план дня", exact: true }).click();
  await expect(page.locator(".page-heading h1")).toHaveText("Сегодня");
});

test("scheduled theme follows local time and manual theme overrides schedule", async ({ page, mock: _mock }) => {
  await page.clock.install({ time: new Date(2026, 8, 5, 19, 59, 50) });
  await page.goto("/new/");
  await page.getByRole("button", { name: "По времени", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-display-theme", "light");
  await page.clock.fastForward(30_000);
  await expect(page.locator("html")).toHaveAttribute("data-display-theme", "dark");
  await page.getByRole("button", { name: "Светлая", exact: true }).click();
  await page.clock.fastForward(30_000);
  await expect(page.locator("html")).toHaveAttribute("data-display-theme", "light");
});
