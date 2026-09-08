export const financeForecastOverviewFixture = () => ({
  readonly_view_scope: { project_id: 7 },
  summary: {
    budget_planned: 1500, budget_committed: 1300, budget_actual: 800,
    budget_forecast: 1700, budget_variance: 200, cash_gap: -250,
    cash_gap_date: "2026-10-04" as string | null, excluded_currency_rows: 0, financial_totals_reliable: true,
  },
  decision_requirements: [] as Array<{ code: string; decision_by: string; message: string }>,
  external_effects: { payment_created: false, posting_created: false, automatic_conversion: false },
  baselines: [{ id: 10, contract_id: 31 }, { id: 11, contract_id: 32 }],
  schedule: [
    { id: 100, baseline_id: 10, title: "Корпус", wbs_parent_id: null, wbs_level: 0, is_summary: true },
    { id: 101, baseline_id: 10, title: "Фундамент", wbs_parent_id: 100, wbs_level: 1, is_summary: false },
    { id: 102, baseline_id: 10, title: "Каркас", wbs_parent_id: 100, wbs_level: 1, is_summary: false },
    { id: 200, baseline_id: 11, title: "Отделка" },
  ],
  budget: [
    { id: 1, contract_id: 31, schedule_item_id: 101, status: "approved", currency: "RUB", planned_amount: 600, committed_amount: 500, actual_amount: 300, forecast_amount: 700 },
    { id: 2, contract_id: 31, schedule_item_id: 102, status: "active", currency: "RUB", planned_amount: 400, committed_amount: 350, actual_amount: 200, forecast_amount: 450 },
    { id: 3, contract_id: 32, schedule_item_id: 200, status: "closed", currency: "RUB", planned_amount: 500, committed_amount: 450, actual_amount: 300, forecast_amount: 550 },
    { id: 4, contract_id: 31, schedule_item_id: 101, status: "proposed", currency: "RUB", planned_amount: 999, committed_amount: 999, actual_amount: 999, forecast_amount: 999 },
  ],
});
