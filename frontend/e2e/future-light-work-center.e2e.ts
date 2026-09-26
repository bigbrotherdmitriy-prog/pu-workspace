import { expect, test, settled } from "./storage-fixtures";

const dashboard = {
  summary: {
    attention: 15,
    documents: 9,
    open_tasks: 8,
    overdue_tasks: 2,
    open_risks: 4,
    pending_decisions: 1,
    drafts: 0,
    open_obligations: 7,
    overdue_obligations: 3,
    upcoming_meetings: 2,
    unread_notifications: 5,
  },
  documents: [],
};

const finance = {
  budget: [], cash_flow: [], procurement: [], acts: [], baselines: [], schedule: [],
  summary: {
    budget_planned: 100_000_000,
    budget_committed: 65_000_000,
    budget_actual: 43_000_000,
    budget_forecast: 98_000_000,
    budget_variance: 2_000_000,
    cash_balance_forecast: 0,
    cash_gap: 0,
    cash_gap_date: null,
    delayed_schedule: 0,
    late_procurement: 0,
    acts_pending: 0,
    pending_payments: 0,
    unlinked_invoices: 0,
  },
};

test("future-light work center uses live totals and works at 1440 and 390", async ({ page, mock: _mock }, testInfo) => {
  await page.route("**/dashboard/project?*", route => route.fulfill({ json: dashboard }));
  await page.route("**/execution/overview?*", route => route.fulfill({ json: finance }));
  await page.goto("/new/");

  const deck = page.locator(".dashboard-overview-deck");
  await expect(deck).toBeVisible();
  await expect(deck.getByRole("heading", { name: "Штаб управления проектом" })).toBeVisible();
  await expect(deck.getByLabel("Требует решения").locator("strong")).toHaveText("15");
  await expect(deck.getByText("43%", { exact: true })).toBeVisible();
  await expect(page.locator(".future-preview-shell")).toBeVisible();
  await expect(deck.getByLabel("Краткая сводка")).toContainText("15 контрольных пунктов");
  await expect(page.getByText("Кассовых разрывов нет.", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Комфорт чтения")).toHaveCount(0);
  const heroBackground = await deck.locator(".dashboard-hero").evaluate(el => getComputedStyle(el).backgroundColor);
  expect(heroBackground).toBe("rgba(0, 0, 0, 0)");
  await expect(deck.locator(".future-twin-model-wrap canvas")).toBeVisible();
  await expect(deck.locator(".future-twin-model-wrap canvas")).toHaveAttribute("data-frame-ready", "true");
  const navBoxes = await page.locator(".shell > aside nav button").evaluateAll(elements => elements.map(el => {
    const rect = el.getBoundingClientRect(); return { top: rect.top, bottom: rect.bottom };
  }));
  for (let i = 1; i < navBoxes.length; i++) expect(navBoxes[i].top).toBeGreaterThanOrEqual(navBoxes[i - 1].bottom);

  const values = await deck.locator(".hq-live-controls b").allTextContents();
  expect(values.map(Number).reduce((sum, value) => sum + value, 0)).toBe(15);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach("future-light-1440", {
    body: await page.screenshot({ fullPage: false }),
    contentType: "image/png",
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await settled(page);
  await expect(page.getByRole("navigation", { name: "Основная мобильная навигация" })).toBeVisible();
  await page.getByRole("button", { name: "Открыть всё меню" }).click();
  await expect(page.locator(".shell > aside nav")).toBeVisible();
  await page.getByRole("button", { name: "Закрыть меню" }).click();

  await page.getByRole("navigation", { name: "Основная мобильная навигация" }).getByRole("button", { name: "ГПР" }).click();
  await expect(page.locator(".gpr-dds-title").getByRole("heading", { name: "ГПР и ДДС" })).toBeVisible();
  await page.getByRole("button", { name: "Закрыть ГПР и ДДС" }).click();
  await page.getByRole("navigation", { name: "Основная мобильная навигация" }).getByRole("button", { name: "Центр" }).click();
  await expect(deck).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await testInfo.attach("future-light-390", {
    body: await page.screenshot({ fullPage: false }),
    contentType: "image/png",
  });
});
