import { expect, test } from "./storage-fixtures";

test("keeps contract GPR budget invoice payment and act in one confirmed chain", async ({ page, mock }) => {
  const budget = {
    id: 81, contract_id: 41, category: "Прямые", description: "Монтаж",
    planned_amount: 100000, committed_amount: 0, actual_amount: 0,
    remaining_amount: 100000, overrun_amount: 0, forecast_amount: 100000,
    currency: "RUB", status: "approved",
  };
  const cashFlow: Record<string, unknown>[] = [];
  const acts: Record<string, unknown>[] = [];
  const proposal: Record<string, unknown> = {
    id: 501, project_id: 2, source_document_id: 601,
    source_document_version_id: 602, source_document_sha256: "a".repeat(64),
    amount: 30000, amount_evidence_quote: "Итого 30 000 руб.", currency: "RUB",
    counterparty: "ООО Поставка", counterparty_evidence_quote: "ООО Поставка",
    payment_purpose: "Материалы", payment_purpose_evidence_quote: "Материалы",
    selected_cost_category_id: 91, category_evidence_quote: "Материалы",
    planned_date: "2026-09-25", confidence: 0.95, extraction_method: "llm",
    target_kind: "cash_flow", status: "proposed", requires_confirmation: true,
  };
  const overview = () => ({
    budget: [budget], cash_flow: cashFlow, procurement: [], acts,
    baselines: [{ id: 71, contract_id: 41, name: "ГПР", version: 1, status: "approved" }],
    schedule: [{ id: 72, baseline_id: 71, title: "Монтаж", planned_progress: 0, actual_progress: 0, status: "planned" }],
    summary: {
      budget_planned: 100000, budget_committed: budget.committed_amount,
      budget_actual: budget.actual_amount, budget_forecast: 100000, budget_variance: 0,
      cash_balance_forecast: 0, cash_gap: 0, cash_gap_date: null,
      delayed_schedule: 0, late_procurement: 0,
      acts_pending: acts.filter(row => ["proposed", "approved"].includes(String(row.status))).length,
      pending_payments: cashFlow.filter(row => row.status === "approved").length,
      unlinked_invoices: 0,
    },
  });

  await page.route("**/projects/2/contracts", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ contracts: [{ id: 41, number: "C-41", title: "Монтаж", contract_kind: "supply" }] }),
  }));
  await page.route("**/execution/**", async route => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/execution/overview") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(overview()) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/document-candidates") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ candidates: [{
        document_id: 601, name: "invoice.pdf", kind: "invoice", score: 95,
        reasons: ["счёт найден"], hints: { amount: "30000", date: "2026-09-25" }, already_linked: false,
      }] }) });
    }
    if (request.method() === "GET" && url.pathname === "/execution/cost-categories") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        categories: [{ id: 91, name: "Прямые", is_active: true, sort_order: 1 }],
      }) });
    }
    if (request.method() === "POST" && url.pathname === "/execution/documents/601/invoice-extraction-proposals") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
    }
    if (request.method() === "PATCH" && url.pathname === "/execution/invoice-extraction-proposals/501") {
      Object.assign(proposal, JSON.parse(request.postData() || "{}"));
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
    }
    if (request.method() === "POST" && url.pathname === "/execution/invoice-extraction-proposals/501/confirm") {
      const payload = JSON.parse(request.postData() || "{}");
      expect(payload).toMatchObject({ contract_id: 41, schedule_item_id: 72, budget_line_id: 81 });
      proposal.status = "confirmed"; proposal.created_cash_flow_id = 82;
      cashFlow.push({
        id: 82, contract_id: 41, schedule_item_id: 72, budget_line_id: 81,
        source_document_id: 601, direction: "outflow", title: "Материалы",
        planned_date: "2026-09-25", planned_amount: 30000, actual_amount: 0,
        category: "Прямые", note: "Материалы", status: "proposed",
      });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(proposal) });
    }
    if (request.method() === "PATCH" && url.pathname === "/execution/cash-flow/82/status") {
      cashFlow[0].status = "approved"; budget.committed_amount = 30000;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: 82, status: "approved" }) });
    }
    if (request.method() === "POST" && url.pathname === "/execution/cash-flow/82/confirm-payment") {
      cashFlow[0].status = "paid"; cashFlow[0].actual_amount = 30000;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: 82, status: "paid", payment_event_id: 83 }) });
    }
    if (request.method() === "POST" && url.pathname === "/execution/acts") {
      const payload = JSON.parse(request.postData() || "{}");
      expect(payload).toMatchObject({ contract_id: 41, budget_line_id: 81 });
      acts.push({ ...payload, id: 84, status: "proposed" });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: 84, status: "proposed" }) });
    }
    if (request.method() === "PATCH" && url.pathname === "/execution/acts/84/status") {
      const payload = JSON.parse(request.postData() || "{}");
      acts[0].status = payload.status;
      if (payload.status === "signed") { budget.actual_amount = 25000; budget.remaining_amount = 75000; }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        id: 84, status: payload.status, budget_line_id: 81, budget_actual_amount: budget.actual_amount,
      }) });
    }
    return route.abort("blockedbyclient");
  });

  await page.goto("/new/");
  await page.getByRole("button", { name: "Исполнение и финансы" }).click();
  await expect(page.locator(".gpr-workspace")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Загрузить бюджет" })).toBeDisabled();
  await page.getByLabel("Финансовый договор").selectOption("41");
  await expect(page.getByRole("button", { name: "Загрузить бюджет" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Загрузить плановый ДДС" })).toBeEnabled();
  await page.getByRole("button", { name: "График работ", exact: true }).click();
  await expect(page.locator(".gpr-workspace")).toBeVisible();
  await expect(page.getByText(/Здесь находятся только календарный план/)).toBeVisible();
  await page.getByRole("button", { name: "Исполнение и финансы" }).click();
  await page.getByRole("button", { name: "Проверить и использовать" }).click();
  await page.getByLabel("Этап ГПР счёта").selectOption("72");
  await page.getByLabel("Строка бюджета счёта").selectOption("81");
  await page.getByRole("button", { name: "Подтвердить и создать предложение" }).click();

  await page.getByRole("tab", { name: "Детализация" }).click();
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  let paymentPrompt = 0;
  page.on("dialog", async dialog => {
    if (dialog.type() === "prompt") {
      paymentPrompt += 1;
      await dialog.accept(paymentPrompt === 1 ? "30000" : "2026-09-25");
      return;
    }
    await dialog.accept();
  });
  await page.getByRole("button", { name: "Оплата", exact: true }).click();
  await expect(page.getByText("paid", { exact: true })).toBeVisible();

  await page.getByLabel("Тип финансовой записи").selectOption("act");
  await page.getByPlaceholder("Название").fill("Акт монтажа");
  await page.getByPlaceholder("Сумма, ₽").fill("25000");
  await page.getByPlaceholder("Номер акта").fill("ACT-84");
  await page.getByLabel("Строка бюджета для акта").selectOption("81");
  await page.getByRole("button", { name: "Создать предложение" }).click();
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await page.getByRole("button", { name: "Подписать", exact: true }).click();

  await expect(page.getByText(/законтрактовано 30[\s ]?000,00 ₽/i)).toBeVisible();
  await expect(page.getByText(/факт работ 25[\s ]?000,00 ₽/i)).toBeVisible();
  expect(mock.unexpected).toEqual([]);
});
