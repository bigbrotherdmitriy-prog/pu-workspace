import type { Page } from "@playwright/test";
import { expect, test, settled } from "./storage-fixtures";

// 13.10.2026 phone review: the header wrapped the project name over four lines,
// the project picker was clipped and the refresh button sat on top of it; the
// "PU" and "+" floating buttons covered the right edge of the content.
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

const sections = ["Центр", "Задачи", "Письма"];

async function openTheme(page: Page, theme: string) {
  await page.goto("/new/");
  await settled(page);
  await page.getByRole("button", { name: theme, exact: true }).first().click();
}

async function show(page: Page, section: string) {
  await page.locator(".android-bottom-nav").getByRole("button", { name: section, exact: true }).click();
  await expect(page.locator(".app-route-loading")).toHaveCount(0);
  await settled(page);
}

async function box(page: Page, selector: string) {
  const value = await page.locator(selector).first().boundingBox();
  expect(value, selector).not.toBeNull();
  return value!;
}

for (const theme of ["Тёмная", "Светлая"]) test(`header is one compact row and floating buttons stay clear (${theme})`, async ({ page, mock: _mock }) => {
  await openTheme(page, theme);
  for (const section of sections) {
    await show(page, section);
    const header = await box(page, ".shell > main > header");
    expect(header.height, `${section}: header height`).toBeLessThanOrEqual(64);
    const parts = [
      page.getByRole("button", { name: "Открыть меню" }),
      page.locator(".shell > main > header h1"),
      page.getByRole("combobox", { name: "Текущий проект" }),
      page.getByRole("button", { name: "Обновить данные проекта" }),
    ];
    let previousRight = 0;
    for (const part of parts) {
      await expect(part).toBeVisible();
      const rect = (await part.boundingBox())!;
      // One row, left to right, no overlaps, nothing outside the screen.
      expect(rect.y, `${section}: header part inside the row`).toBeGreaterThanOrEqual(header.y - 1);
      expect(rect.y + rect.height, `${section}: header part inside the row`).toBeLessThanOrEqual(header.y + header.height + 1);
      expect(rect.x, `${section}: header parts do not overlap`).toBeGreaterThanOrEqual(previousRight - 1);
      expect(rect.x + rect.width).toBeLessThanOrEqual(390);
      previousRight = rect.x + rect.width;
    }
    for (const part of [parts[0], parts[2], parts[3]]) expect((await part.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    // The full project name stays available in the picker's list.
    await expect(page.getByRole("combobox", { name: "Текущий проект" }).locator("option", { hasText: "Persistent Project" })).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), `${section}: no horizontal page scroll`).toBeLessThanOrEqual(390);

    // Scrolled to the end, the content finishes above the floating buttons.
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await settled(page);
    const fabTop = Math.min((await box(page, ".ai-mascot")).y, (await box(page, ".android-upload-fab")).y);
    const contentBottom = await page.locator(".shell > main > .content").evaluate(content => {
      const children = [...content.children].filter(child => child.getBoundingClientRect().height > 0);
      return Math.max(...children.map(child => child.getBoundingClientRect().bottom));
    });
    expect(contentBottom, `${section}: last content block ends above the floating buttons`).toBeLessThanOrEqual(fabTop);
    for (const fab of [".ai-mascot", ".android-upload-fab"]) {
      const rect = await box(page, fab);
      expect(rect.width, fab).toBeGreaterThanOrEqual(44);
      expect(rect.width, fab).toBeLessThanOrEqual(48);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
  }
});

test("visible controls in the content are at least 44 px tall", async ({ page, mock: _mock }) => {
  await openTheme(page, "Светлая");
  for (const section of sections) {
    await show(page, section);
    const small = await page.locator(".shell > main").evaluate(main => [...main.querySelectorAll<HTMLElement>("button, select, input:not([type=checkbox]):not([type=radio]), [role=tab]")]
      .filter(element => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== "hidden"; })
      .filter(element => element.getBoundingClientRect().height < 43.5)
      .map(element => `${element.tagName} "${(element.getAttribute("aria-label") || element.textContent || "").trim().slice(0, 30)}" ${Math.round(element.getBoundingClientRect().height)}px`));
    expect(small, `${section}: tap targets`).toEqual([]);
  }
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, isMobile: false, hasTouch: false });
  test("keeps the full header", async ({ page, mock: _mock }) => {
    await page.goto("/new/");
    await settled(page);
    await page.locator(".shell > aside").getByRole("button", { name: "Задачи", exact: true }).click();
    await expect(page.locator(".shell > main > header .page-kicker")).toBeVisible();
    await expect(page.locator(".android-bottom-nav")).toBeHidden();
  });
});
