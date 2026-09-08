import { expect, start, test } from "./storage-fixtures";
import { cashFixture } from "../src/modules/finance/cashFlowViewsFixtures";

test("actual App shows four consistent DDS views from one scoped read and no mutation", async ({ page, mock }) => {
  mock.reply("GET", "/execution/overview?project_id=2", { body: {
    baselines: [], schedule: [], budget: [], cash_flow: [], procurement: [], acts: [], summary: {
      budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
      budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0, delayed_schedule: 0,
      late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0,
    },
  } });
  mock.reply("GET", "/execution/forecast/2", { status: 503, body: { detail: "Synthetic forecast disabled" } });
  const data = cashFixture(); data.scope.project_id = 2; data.details[0].project_id = 2;
  mock.reply("GET", "/execution/cash-flow/views?project_id=2&date_from=2026-09-01&date_to=2026-09-01", { body: data });
  await start(page); await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await expect(page.getByText(/Показатели ниже относятся ко всему проекту/)).toBeVisible();
  const panel = page.getByRole("region", { name: "Согласованные представления ДДС", exact: true });
  await expect(panel).toBeVisible();
  expect(mock.requests.filter(r => r.path.includes("/cash-flow/views"))).toHaveLength(0);
  await panel.getByLabel("ДДС с", { exact: true }).fill("2026-09-01");
  await panel.getByLabel("ДДС по", { exact: true }).fill("2026-09-01");
  await panel.getByRole("button", { name: "Загрузить период ДДС", exact: true }).click();
  await expect(panel.getByText(/Synthetic cash/)).toBeVisible();
  for (const name of ["Месяцы", "Календарь", "Сводка"]) {
    await panel.getByRole("button", { name, exact: true }).click();
    await expect(panel.getByRole("cell", { name: "90071992547409.91", exact: true })).toHaveCount(2);
  }
  await expect(panel.getByText(/Начальный банковский остаток неизвестен/)).toBeVisible();
  expect(mock.requests.filter(r => r.path.includes("/cash-flow/views"))).toHaveLength(1);
  expect(mock.requests.filter(r => ["POST", "PATCH", "PUT", "DELETE"].includes(r.method))).toHaveLength(0);
  await panel.getByLabel("ДДС по", { exact: true }).fill("2026-09-02");
  await expect(panel.getByRole("table")).toHaveCount(0);
  expect(mock.requests.filter(r => r.path.includes("/cash-flow/views"))).toHaveLength(1);
});
