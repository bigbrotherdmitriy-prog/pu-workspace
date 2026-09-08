import type { StorageApi } from "./storage-fixtures";

export const financeProjectId = 2;

export function financeOverview(projectId = financeProjectId) {
  return {
    readonly_view_scope: { project_id: projectId },
    summary: {
      budget_planned: 1_500_000,
      budget_committed: 1_300_000,
      budget_actual: 800_000,
      budget_forecast: 1_700_000,
      budget_variance: 200_000,
      cash_balance_forecast: -250_000,
      cash_gap: -250_000,
      cash_gap_date: "2026-10-04",
      delayed_schedule: 0,
      late_procurement: 0,
      acts_pending: 0,
      pending_payments: 0,
      unlinked_invoices: 0,
      excluded_currency_rows: 0,
      financial_totals_reliable: true,
    },
    decision_requirements: [
      { code: "retention_treatment", decision_by: "OWNER", message: "Подтвердить правило удержания" },
      { code: "vat_treatment", decision_by: "LEGAL", message: "Подтвердить правило НДС" },
    ],
    external_effects: { payment_created: false, posting_created: false, automatic_conversion: false },
    baselines: [{ id: 10, contract_id: 31, name: "Базовый ГПР", version: 1, status: "approved", is_current: true }],
    schedule: [
      { id: 100, baseline_id: 10, title: "Корпус", wbs_parent_id: null, wbs_level: 0, wbs_order: 0,
        is_summary: true, planned_start: "2026-09-01", planned_finish: "2026-11-30", planned_progress: 40,
        actual_progress: 30, status: "active" },
      { id: 101, baseline_id: 10, title: "Фундамент", wbs_parent_id: 100, wbs_level: 1, wbs_order: 0,
        is_summary: false, planned_start: "2026-09-01", planned_finish: "2026-09-30", planned_progress: 100,
        actual_progress: 90, status: "active" },
      { id: 102, baseline_id: 10, title: "Каркас", wbs_parent_id: 100, wbs_level: 1, wbs_order: 1,
        is_summary: false, planned_start: "2026-10-01", planned_finish: "2026-11-30", planned_progress: 10,
        actual_progress: 0, status: "planned" },
    ],
    budget: [
      { id: 1, record_version: 1, contract_id: 31, schedule_item_id: 101, review_status: "confirmed",
        category: "works", description: "Фундамент", planned_amount: 900_000, committed_amount: 800_000,
        actual_amount: 500_000, forecast_amount: 1_000_000, currency: "RUB", status: "approved" },
      { id: 2, record_version: 1, contract_id: 31, schedule_item_id: 102, review_status: "confirmed",
        category: "works", description: "Каркас", planned_amount: 600_000, committed_amount: 500_000,
        actual_amount: 300_000, forecast_amount: 700_000, currency: "RUB", status: "active" },
    ],
    cash_flow: [{ id: 5, record_version: 1, contract_id: 31, schedule_item_id: 101, budget_line_id: 1,
      review_status: "confirmed", direction: "out", title: "Оплата работ", planned_date: "2026-10-04",
      planned_amount: 250_000, actual_amount: 0, status: "planned" }],
    procurement: [],
    acts: [],
  };
}

export function installFinanceScenario(mock: StorageApi, overview: ReturnType<typeof financeOverview>) {
  mock.reply("GET", `/projects/${financeProjectId}/contracts`, { body: { contracts: [{
    id: 31,
    number: "SYN-31",
    title: "Подтверждённый договор",
    status: "active",
    contract_kind: "customer",
    parent_contract_id: null,
    record_version: 1,
  }] } });
  mock.reply("GET", `/execution/overview?project_id=${financeProjectId}`, { body: overview });
  mock.reply("GET", `/execution/forecast/${financeProjectId}`, { status: 503, body: { detail: "Synthetic forecast disabled" } });
  mock.reply("GET", `/execution/document-candidates?project_id=${financeProjectId}`, { body: { candidates: [] } });
}
