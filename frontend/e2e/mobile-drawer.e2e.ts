import { chooseThemeOnPhone, expect, test, settled } from "./storage-fixtures";

// 13.10.2026: on phones the "Ещё" tab opened the drawer underneath its own
// backdrop (theme sheets pinned the Work Center sidebar at z-index 20 and gave
// the backdrop an opaque button surface), so the menu looked empty.
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

for (const theme of ["Тёмная", "Светлая"]) test(`"Ещё" shows a usable drawer (${theme})`, async ({ page, mock }) => {
  await page.goto("/new/");
  await settled(page);
  await chooseThemeOnPhone(page, theme);
  await page.getByRole("button", { name: "Открыть всё меню" }).click();
  const drawer = page.locator(".shell > aside.mobile-open");
  await expect(drawer).toBeVisible();
  // The drawer, not the backdrop, is what a tap inside the menu hits.
  const hit = await page.evaluate(() => {
    const el = document.elementFromPoint(150, 300);
    return Boolean(el && el.closest(".shell > aside"));
  });
  expect(hit).toBe(true);
  const backdrop = await page.locator(".mobile-drawer-backdrop").evaluate(el => getComputedStyle(el).backgroundColor);
  expect(backdrop).toMatch(/rgba\(.+, 0\.\d+\)/);
  await drawer.getByRole("button", { name: "Договоры", exact: true }).click();
  await expect(drawer).toHaveCount(0);
});
