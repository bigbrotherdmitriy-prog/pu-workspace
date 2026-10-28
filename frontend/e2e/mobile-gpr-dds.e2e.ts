import type { Page } from "@playwright/test";
import { chooseThemeOnPhone, expect, test } from "./storage-fixtures";

// 13.10.2026 phone review: ГПР showed the wide table and the Gantt chart with a
// wide "№" column and the editing toolbar on top; ДДС sums ran off the screen
// in a monospace font.
const task = (id: number, title: string, start: string, finish: string, extra: Record<string, unknown> = {}) => ({
  id, baseline_id: 10, title, sort_order: id, planned_start: start, planned_finish: finish,
  duration_days: Math.round((Date.parse(finish) - Date.parse(start)) / 86_400_000) + 1,
  planned_progress: 0, actual_progress: 0, status: "planned", ...extra,
});
const schedule = [
  task(1, "Подготовительные работы", "2026-09-01", "2026-09-12", { actual_progress: 100 }),
  task(2, "Мобилизация и устройство городка строителей", "2026-09-01", "2026-09-05", { parent_id: 1, actual_progress: 100 }),
  task(3, "Геодезическая разбивка осей", "2026-09-06", "2026-09-12", { parent_id: 1, predecessor_ids: "2", actual_progress: 100 }),
  task(4, "Навесной вентилируемый фасад", "2026-09-13", "2026-11-20", { predecessor_ids: "3", actual_progress: 35 }),
  task(5, "Монтаж подсистемы секций 1–3", "2026-09-13", "2026-10-15", { parent_id: 4, predecessor_ids: "3", actual_progress: 60 }),
  task(6, "Облицовка керамогранитом", "2026-10-16", "2026-11-20", { parent_id: 4, predecessor_ids: "5", actual_progress: 5 }),
  task(7, "Сдача фасада секции 1", "2026-11-20", "2026-11-20", { parent_id: 6, predecessor_ids: "6", is_milestone: true, duration_days: 0 }),
  task(8, "Благоустройство", "2026-10-01", "2026-10-20", { actual_progress: 0 }),
];
const cash = (id: number, direction: string, date: string, amount: number, category: string, title = category) => ({
  id, contract_id: 41, direction, title, planned_date: date, planned_amount: amount, actual_amount: 0, currency: "RUB",
  status: "approved", record_version: 1, object_name: "Корпус 1", category,
});
const cashFlow = [
  cash(31, "inflow", "2026-10-05", 12_500_000, "Аванс заказчика"),
  cash(32, "outflow", "2026-10-10", 4_870_350.5, "Материалы фасада", "Керамогранит"),
  cash(33, "outflow", "2026-10-18", 1_130_000, "Материалы фасада", "Подсистема"),
  cash(34, "outflow", "2026-10-25", 2_400_000, "Субподряд: монтаж навесного вентилируемого фасада"),
  cash(35, "outflow", "2026-11-25", 3_100_000, "Субподряд: монтаж навесного вентилируемого фасада"),
  cash(36, "inflow", "2026-11-30", 9_000_000, "Оплата по актам КС-2"),
];

async function withData(page: Page) {
  await page.route("**/projects/2/contracts", route => route.fulfill({ json: { contracts: [
    { id: 41, record_version: 1, number: "C-41", title: "Навесной фасад", contract_kind: "supply" },
  ] } }));
  await page.route("**/execution/overview?*", route => route.fulfill({ json: {
    baselines: [{ id: 10, contract_id: 41, name: "ГПР фасада", version: 1, status: "draft", source_file_name: "Фасад.mpp" }],
    schedule, cash_flow: cashFlow, budget: [], acts: [], procurement: [], summary: {},
  } }));
}

async function open(page: Page, theme: string, tab: "ГПР" | "ДДС") {
  await withData(page);
  await page.goto("/new/");
  await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
  await chooseThemeOnPhone(page, theme);
  await page.locator(".android-bottom-nav").getByRole("button", { name: tab, exact: true }).click();
  await expect(page.getByRole("tab", { name: tab, exact: true })).toHaveAttribute("aria-selected", "true");
}

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => {
    const overlay = document.querySelector<HTMLElement>(".gpr-dds-overlay");
    const panels = [...document.querySelectorAll<HTMLElement>(".gpr-dds-panel:not([hidden])")];
    return {
      page: document.documentElement.scrollWidth,
      overlay: overlay ? overlay.scrollWidth - overlay.clientWidth : 0,
      panels: panels.map(panel => panel.scrollWidth - panel.clientWidth),
    };
  });
  expect(widths.page, "no horizontal page scroll").toBeLessThanOrEqual(390);
  expect(widths.overlay, "no horizontal scroll in the ГПР/ДДС screen").toBeLessThanOrEqual(0);
  for (const extra of widths.panels) expect(extra, "no horizontal scroll in the panel").toBeLessThanOrEqual(0);
}

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  for (const theme of ["Тёмная", "Светлая"]) {
    test(`ГПР is a tree list with actions behind a button (${theme})`, async ({ page, mock: _mock }) => {
      await open(page, theme, "ГПР");
      const list = page.getByRole("list", { name: "Этапы и вехи ГПР" });
      await expect(list).toBeVisible();
      await expect(page.locator(".gpr-split")).toBeHidden();
      await noHorizontalScroll(page);

      const items = list.getByRole("listitem");
      await expect(items).toHaveCount(8);
      const lefts = await items.locator(".gpr-mobile-task").evaluateAll(buttons => buttons.map(button => button.getBoundingClientRect().left));
      // Depth 0 → 1 → 2 steps of 12–16 px, never more.
      for (const [parent, child] of [[0, 1], [3, 4], [5, 6]]) {
        const step = lefts[child] - lefts[parent];
        expect(step, `indent ${parent}→${child}`).toBeGreaterThanOrEqual(12);
        expect(step, `indent ${parent}→${child}`).toBeLessThanOrEqual(16);
      }
      const facade = items.nth(4);
      await expect(facade).toContainText("Монтаж подсистемы секций 1–3");
      await expect(facade).toContainText("13.09.26 — 15.10.26");
      await expect(facade.getByLabel("Выполнено 60%")).toBeVisible();
      await expect(facade.getByText("Критический путь")).toBeVisible();
      await expect(items.nth(7).getByText("Критический путь")).toHaveCount(0);
      await expect(items.nth(6)).toContainText("Веха · 20.11.26");
      const number = (await items.nth(0).locator(".gpr-mobile-no").boundingBox())!;
      expect(number.width, "the number column stays narrow").toBeLessThanOrEqual(24);
      for (const button of await items.locator(".gpr-mobile-task").all()) {
        const box = (await button.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x + box.width).toBeLessThanOrEqual(390);
      }

      // Editing toolbar is behind "Действия".
      const toolbar = page.locator(".gpr-toolbar");
      await expect(toolbar).toBeHidden();
      const actions = page.getByRole("button", { name: "Действия" });
      await expect(actions).toHaveAttribute("aria-expanded", "false");
      await actions.click();
      await expect(toolbar).toBeVisible();
      for (const name of [/Отступ/, /Выступ/, /Веха/, /Экспорт ГПР CSV/]) await expect(toolbar.getByRole("button", { name })).toBeVisible();
      await expect(toolbar.getByText("Масштаб")).toBeVisible();
      await expect(toolbar.getByLabel("Baseline для сравнения")).toBeVisible();
      await noHorizontalScroll(page);
      await actions.click();
      await expect(toolbar).toBeHidden();

      // Tapping a stage selects it for the editor below the list.
      await items.nth(7).locator(".gpr-mobile-task").click();
      await expect(page.getByLabel("Название задачи")).toHaveValue("Благоустройство");
    });

    test(`ДДС shows one month as category → sum with totals (${theme})`, async ({ page, mock: _mock }) => {
      await open(page, theme, "ДДС");
      await page.getByRole("tab", { name: "Таблица ДДС" }).click();
      const month = page.locator(".dds-mobile-month");
      await expect(month).toBeVisible();
      await expect(page.locator(".dds-calendar")).toBeHidden();
      await page.getByLabel("Месяц ДДС").selectOption("2026-10");
      const outflow = month.getByRole("region", { name: "Затраты" });
      await expect(outflow.getByRole("listitem")).toHaveCount(2);
      await expect(outflow.getByRole("listitem").first()).toContainText("Материалы фасада");
      await expect(outflow.getByRole("listitem").first()).toContainText("6 000 350,50 ₽");
      await expect(month.getByLabel(/Итоги за/)).toContainText("12 500 000,00 ₽");
      await noHorizontalScroll(page);
      // Every sum is fully on screen and set in the regular font.
      for (const sum of await month.locator("b, dd").all()) {
        const box = (await sum.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width, `sum ${await sum.textContent()} on screen`).toBeLessThanOrEqual(390);
        expect(await sum.evaluate(element => getComputedStyle(element).fontFamily)).not.toMatch(/mono|consolas/i);
      }
      await page.getByLabel("Месяц ДДС").selectOption("2026-11");
      await expect(month.getByLabel(/Итоги за/)).toContainText("Остаток на конец месяца");
      await expect(month.getByLabel(/Итоги за/)).toContainText("9 999 649,50 ₽");

      // Detail rows are cards with the plan sum visible.
      await page.getByRole("tab", { name: "Детализация" }).click();
      const plan = page.locator("td[data-label='План']", { hasText: "4 870 350,50 ₽" });
      await expect(plan).toBeVisible();
      const planBox = (await plan.boundingBox())!;
      expect(planBox.x + planBox.width).toBeLessThanOrEqual(390);
      expect(await plan.evaluate(element => getComputedStyle(element).fontFamily)).not.toMatch(/mono|consolas/i);
      await noHorizontalScroll(page);
    });
  }
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });
  test("keeps the table, the Gantt chart, the toolbar and the year calendar", async ({ page, mock: _mock }) => {
    await withData(page);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
    await expect(page.locator(".gpr-split")).toBeVisible();
    await expect(page.locator(".gpr-chart")).toBeVisible();
    await expect(page.locator(".gpr-toolbar")).toBeVisible();
    await expect(page.getByRole("list", { name: "Этапы и вехи ГПР" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Действия" })).toBeHidden();
    await page.getByRole("tab", { name: "ДДС", exact: true }).click();
    await page.getByRole("tab", { name: "Таблица ДДС" }).click();
    await expect(page.locator(".dds-calendar")).toBeVisible();
    await expect(page.locator(".dds-mobile-month")).toBeHidden();
    await page.getByRole("tab", { name: "Детализация" }).click();
    await expect(page.locator(".dds-table thead").last()).toBeVisible();
  });
});
