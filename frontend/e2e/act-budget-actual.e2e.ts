import { expect, test } from "./storage-fixtures";

test("links an act, signs it into budget actual, then revokes the projection", async ({ page, mock }) => {
  const budget = {
    id: 81, contract_id: 41, category: "СМР", description: "Монтаж",
    planned_amount: 100000, committed_amount: 0, actual_amount: 0,
    remaining_amount: 100000, overrun_amount: 0, forecast_amount: 100000,
    currency: "RUB", status: "approved",
  };
  const acts: Record<string, unknown>[] = [];
  const overview = () => ({
    budget: [budget], cash_flow: [], procurement: [], acts, baselines: [], schedule: [],
    summary: {
      budget_planned: 100000, budget_committed: 0, budget_actual: budget.actual_amount,
      budget_forecast: 100000, budget_variance: 0, cash_balance_forecast: 0,
      cash_gap: 0, cash_gap_date: null, delayed_schedule: 0, late_procurement: 0,
      acts_pending: acts.filter(row => ["proposed", "approved"].includes(String(row.status))).length,
      pending_payments: 0, unlinked_invoices: 0,
    },
  });

  await page.route("**/projects/2/contracts", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ contracts: [{ id: 41, number: "C-1", title: "Монтаж", contract_kind: "supplier" }] }),
  }));
  await page.route("**/execution/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/execution/overview") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(overview()) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/document-candidates") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ candidates: [] }) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/cost-categories") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ categories: [] }) });
    }
    if (request.method() === "POST" && url.pathname === "/execution/acts") {
      const payload = JSON.parse(request.postData() || "{}");
      expect(payload.budget_line_id).toBe(81);
      acts.push({ ...payload, id: 91, status: "proposed" });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: 91, status: "proposed" }) });
    }
    const match = url.pathname.match(/^\/execution\/acts\/(\d+)\/status$/);
    if (request.method() === "PATCH" && match) {
      const payload = JSON.parse(request.postData() || "{}");
      acts[0].status = payload.status;
      budget.actual_amount = payload.status === "signed" ? 40000 : 0;
      budget.remaining_amount = 100000 - budget.actual_amount;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        id: 91, status: payload.status, budget_line_id: 81,
        budget_actual_amount: budget.actual_amount,
      }) });
    }
    return route.abort("blockedbyclient");
  });

  await page.goto("/new/");
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  const dds = page.getByRole("tabpanel", { name: "ДДС" });
  await dds.getByLabel("Финансовый договор").selectOption("41");
  await dds.getByRole("button", { name: "Расход", exact: true }).click();
  await dds.getByLabel("Тип финансовой записи").selectOption("act");
  await dds.getByPlaceholder("Название").fill("Акт монтажа");
  await dds.getByPlaceholder("Сумма, ₽").fill("40000");
  await dds.getByPlaceholder("Номер акта").fill("A-91");
  await dds.getByLabel("Строка бюджета для акта").selectOption("81");
  await dds.getByRole("button", { name: "Создать предложение" }).click();

  await dds.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await dds.getByRole("button", { name: "Подписать", exact: true }).click();
  await expect(dds.getByText(/факт работ 40[\s ]?000,00 ₽/)).toBeVisible();

  await dds.getByRole("button", { name: "Отменить подписание" }).click();
  await expect(dds.getByText(/факт работ 0,00 ₽/)).toBeVisible();
  expect(acts[0].status).toBe("approved");
  expect(mock.unexpected).toEqual([]);
});
