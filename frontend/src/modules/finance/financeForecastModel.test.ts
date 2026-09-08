import { describe, expect, it } from "vitest";
import { buildFinanceForecastView } from "./financeForecastModel";
import { financeForecastOverviewFixture as overviewFixture } from "./financeForecastFixtures";

describe("buildFinanceForecastView", () => {
  it("builds exact totals plus contract and recursive WBS rollups", () => {
    const view = buildFinanceForecastView(overviewFixture(), 7);
    expect(view.totals).toEqual({ planned: 1500, committed: 1300, actual: 800, forecast: 1700, variance: 200 });
    expect(view.contracts).toEqual(expect.arrayContaining([
      expect.objectContaining({ contractId: 31, planned: 1000, forecast: 1150, variance: 150 }),
      expect.objectContaining({ contractId: 32, planned: 500, forecast: 550, variance: 50 }),
    ]));
    expect(view.wbs.find((row) => row.scheduleItemId === 100)).toMatchObject({ planned: 1000, committed: 850, actual: 500, forecast: 1150, variance: 150, isSummary: true });
    expect(view.reliability).toBe("reliable");
  });

  it("requires an exact local project scope", () => {
    expect(() => buildFinanceForecastView(overviewFixture(), 8)).toThrow("invalid_finance_overview");
    const raw = overviewFixture(); delete (raw as Partial<typeof raw>).readonly_view_scope;
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("invalid_finance_overview");
  });

  it("fails closed when summary and accepted RUB rows disagree", () => {
    const raw = overviewFixture(); raw.summary.budget_forecast = 1701; raw.summary.budget_variance = 201;
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("finance_summary_mismatch");
  });

  it("never treats another currency as converted RUB", () => {
    const raw = overviewFixture(); raw.budget[0].currency = "USD";
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("finance_summary_mismatch");
  });

  it("makes owner and legal decisions visible", () => {
    const raw = overviewFixture();
    raw.summary.financial_totals_reliable = false;
    raw.decision_requirements = [{ code: "vat_treatment", decision_by: "LEGAL", message: "Подтвердить учёт НДС" }];
    const view = buildFinanceForecastView(raw, 7);
    expect(view.reliability).toBe("decision_required");
    expect(view.decisions).toEqual([{ code: "vat_treatment", decisionBy: "LEGAL", message: "Подтвердить учёт НДС" }]);
  });

  it("rejects unsafe external-effect claims", () => {
    const raw = overviewFixture(); (raw.external_effects as { payment_created: boolean }).payment_created = true;
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("unsafe_external_effects");
  });

  it("rejects cross-contract schedule linkage", () => {
    const raw = overviewFixture(); raw.budget[0].contract_id = 32;
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("budget_contract_schedule_mismatch");
  });

  it("rejects malformed WBS hierarchy", () => {
    const raw = overviewFixture(); raw.schedule[1].wbs_parent_id = 200;
    expect(() => buildFinanceForecastView(raw, 7)).toThrow("invalid_wbs_parent");
  });

  it("rejects duplicate budget identity and direct summary linkage", () => {
    const duplicate = overviewFixture(); duplicate.budget[1].id = 1;
    expect(() => buildFinanceForecastView(duplicate, 7)).toThrow("invalid_budget_row");
    const summaryLink = overviewFixture(); summaryLink.budget[0].schedule_item_id = 100;
    expect(() => buildFinanceForecastView(summaryLink, 7)).toThrow("summary_budget_link_forbidden");
  });

  it("requires a date exactly when a cash gap exists", () => {
    const missing = overviewFixture(); missing.summary.cash_gap_date = null;
    expect(() => buildFinanceForecastView(missing, 7)).toThrow("invalid_cash_gap_basis");
    const stale = overviewFixture(); stale.summary.cash_gap = 0;
    expect(() => buildFinanceForecastView(stale, 7)).toThrow("invalid_cash_gap_basis");
  });
});
