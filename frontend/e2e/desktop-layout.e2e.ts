import type { Page } from "@playwright/test";
import { expect, test, settled } from "./storage-fixtures";

// Owner report 14.10.2026 (Yandex, desktop, zoom 67–80 %): with a long
// project name the header controls ran past the right edge on every section
// and the cards looked cut off. The project name in the kicker sized the
// whole page heading and squeezed the header actions to zero width.
const longName = "Договор на модернизацию системы электроснабжения и внутренних инженерных сетей административно-производственного комплекса, этап 2";
const sections = ["Рабочий центр", "Запуск проекта", "AI Secretary", "Задачи", "Письма", "Договоры"];

async function measure(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector(".shell > main")!.getBoundingClientRect();
    const header = document.querySelector(".shell > main > header")!.getBoundingClientRect();
    const outside: string[] = [];
    for (const element of document.querySelectorAll<HTMLElement>(".shell > main *")) {
      const box = element.getBoundingClientRect();
      if (!box.width || !box.height || getComputedStyle(element).position === "fixed") continue;
      if (box.right > main.right + 1 || box.left < main.left - 1) outside.push(`${element.tagName}.${element.className}`);
    }
    const cards = [...document.querySelectorAll<HTMLElement>(".shell > main > .content > :not(.display-preferences), .shell > main > .content .card")]
      .map(element => element.getBoundingClientRect()).filter(box => box.width > 0);
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      headerHeight: header.height,
      outside: outside.slice(0, 5),
      cardGap: cards.length ? Math.min(...cards.map(box => main.right - box.right)) : Infinity,
    };
  });
}

for (const [width, height] of [[1220, 760], [1472, 800], [1920, 1080]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } });
    for (const theme of ["Тёмная", "Светлая"]) test(`long project name keeps the header and content inside main (${theme})`, async ({ page, mock }) => {
      mock.projectRows = [{ id: 1, name: "Persistent Project" }, { id: 2, name: longName }];
      await page.goto("/new/");
      await settled(page);
      await page.getByRole("button", { name: theme, exact: true }).first().click();
      for (const section of sections) {
        await page.locator(".shell > aside").getByRole("button", { name: section, exact: true }).click();
        await expect(page.locator(".app-route-loading")).toHaveCount(0);
        await settled(page);
        const result = await measure(page);
        expect(result.overflow, `${section}: no horizontal page scroll`).toBeLessThanOrEqual(0);
        expect(result.outside, `${section}: nothing inside main crosses its edges`).toEqual([]);
        expect(result.headerHeight, `${section}: header stays one row`).toBeLessThanOrEqual(84);
        expect(result.cardGap, `${section}: cards keep a right gutter`).toBeGreaterThanOrEqual(12);
      }
      // The full name is still available: in the picker and as the kicker's tooltip.
      await expect(page.getByRole("combobox", { name: "Текущий проект" })).toHaveValue("2");
      await expect(page.locator(".shell > main > header .page-kicker")).toHaveAttribute("title", longName);
    });
  });
}
