import type { Page } from "@playwright/test";
import { expect, test, settled } from "./storage-fixtures";

// 13.10.2026 phone review: on every screen the top half was taken by the sync
// card ("Синхронизация готова" + a full-width button) and the row of theme
// buttons, before any real content. Phones now show sync as a header dot (a
// one-row banner only when offline or something waits) and keep the theme
// controls in the "Ещё" drawer. Desktop stays as it was.

const themeButtons = ["Светлая", "Тёмная", "По времени", "Режим комфорта"];

async function show(page: Page, section: string) {
  await page.locator(".android-bottom-nav").getByRole("button", { name: section, exact: true }).click();
  await expect(page.locator(".app-route-loading")).toHaveCount(0);
  await settled(page);
}

// The e2e container has no network, so Chromium reports navigator.onLine as
// false; pin the connection state the test is about before the app starts.
async function connection(page: Page, online: boolean) {
  await page.addInitScript(value => {
    Object.defineProperty(Navigator.prototype, "onLine", { configurable: true, get: () => value });
  }, online);
}

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("content starts near the top: no theme row, no idle sync card", async ({ page, mock: _mock }, info) => {
    await connection(page, true);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    for (const section of ["Центр", "Задачи"]) {
      await show(page, section);
      const main = page.locator(".shell > main");
      for (const name of themeButtons) await expect(main.getByRole("button", { name, exact: true }), `${section}: ${name}`).toHaveCount(0);
      await expect(page.locator(".mobile-sync-panel")).toHaveCount(0);
      await expect(main.getByRole("button", { name: "Синхронизировать сейчас" })).toHaveCount(0);
      // The settled sync state is a dot on the refresh button.
      await expect(page.getByRole("button", { name: "Обновить данные проекта" })).toHaveAttribute("data-sync", "settled");
      const firstTop = await page.locator(".shell > main > .content").evaluate(content => {
        const first = [...content.children].find(child => child.getBoundingClientRect().height > 0);
        return first ? first.getBoundingClientRect().top : Number.POSITIVE_INFINITY;
      });
      expect(firstTop, `${section}: first content block starts in the top quarter`).toBeLessThanOrEqual(844 * 0.25);
      await page.screenshot({ path: info.outputPath(`top-area-${section}-390.png`) });
    }
  });

  test("theme switching works from the \"Ещё\" drawer", async ({ page, mock: _mock }, info) => {
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await page.getByRole("button", { name: "Открыть всё меню" }).click();
    const drawer = page.locator(".shell > aside.mobile-open");
    const appearance = drawer.getByRole("region", { name: "Оформление" });
    await expect(appearance).toBeVisible();
    await appearance.getByRole("button", { name: "Тёмная", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-display-theme", "dark");
    await expect(appearance.getByRole("button", { name: "Тёмная", exact: true })).toHaveAttribute("aria-pressed", "true");
    await appearance.getByRole("button", { name: "Режим комфорта" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-comfort", "true");
    await appearance.getByRole("button", { name: "Светлая", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-display-theme", "light");
    // The controls stay inside the drawer's width and are real tap targets.
    for (const name of themeButtons) {
      const rect = (await appearance.getByRole("button", { name, exact: true }).boundingBox())!;
      expect(rect.height, name).toBeGreaterThanOrEqual(44);
      expect(rect.x + rect.width, name).toBeLessThanOrEqual(390);
    }
    await page.screenshot({ path: info.outputPath("top-area-drawer-390.png") });
    // Navigation from the drawer still works and closes it.
    await drawer.getByRole("button", { name: "Договоры", exact: true }).click();
    await expect(drawer).toHaveCount(0);
  });

  test("offline shows a one-row sync banner with the action", async ({ page, mock: _mock }, info) => {
    // navigator.onLine=false alone is no longer "offline" while the server
    // answers (Android VPN false-offline, #177), and the mocked API always
    // answers. So go offline the way a phone does: the browser drops the
    // network and fires "offline", and the /health probe gets no answer.
    // The probe is held open: the banner must show before it returns.
    let finishProbe: () => Promise<void> = async () => {};
    await page.route("**/health*", route => new Promise<void>(done => {
      finishProbe = async () => { await route.abort("internetdisconnected"); done(); };
    }));
    await connection(page, true);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await show(page, "Задачи");
    await page.evaluate(() => {
      Object.defineProperty(Navigator.prototype, "onLine", { configurable: true, get: () => false });
      window.dispatchEvent(new Event("offline"));
    });
    const banner = page.locator(".mobile-sync-panel");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Офлайн · несинхронизировано: 0");
    const action = banner.getByRole("button", { name: "Синхронизировать сейчас" });
    await expect(action).toBeVisible();
    await expect(action).toBeDisabled();
    await expect(page.getByRole("button", { name: "Обновить данные проекта" })).toHaveAttribute("data-sync", "offline");
    const header = (await page.locator(".shell > main > header").boundingBox())!;
    const rect = (await banner.boundingBox())!;
    expect(rect.y, "banner right under the header").toBeLessThanOrEqual(header.y + header.height + 12);
    expect(rect.height, "one-row banner").toBeLessThanOrEqual(60);
    const actionBox = (await action.boundingBox())!;
    expect(actionBox.height).toBeGreaterThanOrEqual(44);
    expect(actionBox.x + actionBox.width).toBeLessThanOrEqual(390);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), "no horizontal page scroll").toBeLessThanOrEqual(390);
    await page.screenshot({ path: info.outputPath("top-area-offline-390.png") });
    // The probe fails too: the offline state is confirmed, not cleared.
    await finishProbe();
    await expect(banner).toContainText("Офлайн");
  });
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, isMobile: false, hasTouch: false });

  test("keeps the theme row and the sync panel", async ({ page, mock: _mock }) => {
    await connection(page, true);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await page.locator(".shell > aside").getByRole("button", { name: "Задачи", exact: true }).click();
    const content = page.locator(".shell > main > .content");
    for (const name of themeButtons) await expect(content.getByRole("button", { name, exact: true })).toBeVisible();
    await expect(page.locator(".shell > aside .drawer-appearance")).toHaveCount(0);
    const panel = page.locator(".shell > main > .mobile-sync-panel");
    await expect(panel).toBeVisible();
    await expect(panel).not.toHaveClass(/compact/);
    await expect(panel.getByRole("button", { name: "Синхронизировать сейчас" })).toHaveText("Синхронизировать сейчас");
    await content.getByRole("button", { name: "Тёмная", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-display-theme", "dark");
  });
});
