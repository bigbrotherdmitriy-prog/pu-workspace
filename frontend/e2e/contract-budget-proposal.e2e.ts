import { expect, test } from "./storage-fixtures";

test("proposes one contract total and manager confirms it into budget", async ({ page, mock }) => {
  const proposal: Record<string, unknown> = {
    id: 701, contract_id: 41, contract_record_version: 1, operation: "create",
    amount: 100000, advance_amount: 20000, retention_percent: 5, currency: "RUB",
    description: "Договор C-41: Поставка", selected_cost_category_id: null,
    status: "proposed", requires_confirmation: true,
  };
  const contract = {
    id: 41, record_version: 1, number: "C-41", title: "Поставка",
    contract_kind: "supply", amount: 100000, advance_amount: 20000,
    retention_percent: 5, status: "active", budget_proposals: [] as Record<string, unknown>[],
    version_history: [], analysis: { source_ready: false, tasks: 0, obligations: 0, risks: 0, decisions: 0 },
  };
  const budget: Record<string, unknown>[] = [];

  await page.route("**/projects/2/contracts", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ contracts: [contract] }),
  }));
  await page.route("**/projects/2/contracts/41/budget-proposals", async route => {
    expect(route.request().method()).toBe("POST");
    contract.budget_proposals = [proposal];
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
  });
  await page.route("**/contract-budget-proposals/701", async route => {
    expect(route.request().method()).toBe("PATCH");
    const payload = JSON.parse(route.request().postData() || "{}");
    expect(payload).toEqual({ amount: 100000, description: "Договор C-41: Поставка", selected_cost_category_id: 91 });
    Object.assign(proposal, payload);
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
  });
  await page.route("**/contract-budget-proposals/701/confirm", async route => {
    expect(route.request().method()).toBe("POST");
    proposal.status = "confirmed"; proposal.created_budget_line_id = 801;
    budget.push({
      id: 801, contract_id: 41, cost_category_id: 91, category: "Материалы",
      description: proposal.description, planned_amount: proposal.amount,
      committed_amount: 0, actual_amount: 0, forecast_amount: proposal.amount,
      currency: "RUB", status: "proposed",
    });
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
  });
  await page.route("**/execution/**", async route => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/execution/cost-categories") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        categories: [{ id: 91, name: "Материалы", is_active: true }],
      }) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/overview") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        budget, cash_flow: [], procurement: [], acts: [], baselines: [], schedule: [],
        summary: { budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
          budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0, cash_gap_date: null,
          delayed_schedule: 0, late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0 },
      }) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/document-candidates") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ candidates: [] }) });
    }
    return route.abort("blockedbyclient");
  });

  await page.goto("/new/");
  await page.getByRole("button", { name: "Договоры", exact: true }).click();
  await page.getByRole("button", { name: "Предложить бюджет по договору" }).click();
  await expect(page.getByText("Предложение строки бюджета")).toBeVisible();
  await expect(page.getByText(/Аванс 20[\s ]?000,00 ₽; удержание 5% — справочно/)).toBeVisible();
  await page.getByLabel("Категория предложения бюджета").selectOption("91");
  await page.getByRole("button", { name: "Подтвердить бюджет" }).click();
  await expect(page.getByText(/создана одна строка бюджета/i)).toBeVisible();
  expect(budget).toHaveLength(1);
  expect(contract.budget_proposals[0].status).toBe("confirmed");
  expect(mock.unexpected).toEqual([]);
});
