import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ForecastPanel } from "./ForecastPanel";
import { parseForecastReport, type ForecastReport } from "./types";

afterEach(cleanup);

const source = {
  entity_type: "cash_flow_entry",
  entity_id: 31,
  fields: ["planned_date", "planned_amount"],
  state: "approved",
  evidence_exact: true,
  evidence: [{
    evidence_id: "evidence-31-long-id",
    source_version_id: "version-31",
    confidence: 0.94,
    verification: "verified",
    page: 2,
    coordinates: [10, 20, 110, 50],
  }],
};

const report: ForecastReport = {
  forecast_id: "forecast-immutable-hash",
  project_id: 7,
  as_of: "2026-09-10",
  publication_state: "draft",
  advisory_only: true,
  can_trigger_actions: false,
  requires_human_confirmation: true,
  confidence: { score: 0.88, band: "high", formula: "mean", low_confidence_threshold: 0.7 },
  schedule: {
    formula: "actual; else progress; else plan",
    predicted_finish: "2026-09-20",
    stages: [{
      id: 11,
      title: "Synthetic installation",
      planned_finish: "2026-09-12",
      predicted_finish: "2026-09-20",
      actual_progress: 50,
      formula: "linear_progress_extrapolation",
      formula_description: "remaining = ceil(elapsed × rest / progress)",
      confidence: 0.82,
      risks: ["predicted_delay"],
      sources: [source],
    }],
  },
  budget: {
    formula: "sum(max(plan, committed, actual, declared_forecast))",
    planned_total: "100000.00",
    forecast_total: "115000.00",
    variance: "15000.00",
    lines: [{
      id: 21,
      description: "Synthetic works",
      currency: "RUB",
      planned_amount: "100000.00",
      forecast_amount: "115000.00",
      variance: "15000.00",
      formula: "max(plan, committed, actual, declared_forecast)",
      confidence: 0.82,
      sources: [{ ...source, entity_type: "budget_line", entity_id: 21 }],
    }],
  },
  cash_flow: {
    formula: "running_balance(d) = inflow - outflow",
    opening_balance: "0.00",
    closing_balance: "-30000.00",
    minimum_balance: "-30000.00",
    cash_gap_date: "2026-09-11",
    events: [{
      id: 31,
      title: "Synthetic invoice",
      date: "2026-09-11",
      direction: "outflow",
      amount: "80000.00",
      value_kind: "planned",
      running_balance: "-30000.00",
      confidence: 0.84,
      risks: [],
      sources: [source],
    }],
  },
  risks: [{
    code: "cash_gap",
    severity: "critical",
    explanation: "Synthetic cash gap explanation.",
    sources: [source],
  }],
  manual_confirmation: {
    binding: "forecast-immutable-hash",
    required_before: ["publish_forecast", "financial_action"],
    reason: "advisory",
    persistence_available: false,
  },
};

describe("ForecastPanel", () => {
  it("shows schedule, budget, cash-flow formulas and risk reasons", () => {
    render(<ForecastPanel report={report} />);

    expect(screen.getByRole("heading", { name: "Сроки и денежный поток" })).toBeInTheDocument();
    expect(screen.getByText("actual; else progress; else plan")).toBeInTheDocument();
    expect(screen.getByText("sum(max(plan, committed, actual, declared_forecast))")).toBeInTheDocument();
    expect(screen.getByText("running_balance(d) = inflow - outflow")).toBeInTheDocument();
    expect(screen.getByText("Synthetic cash gap explanation.")).toBeInTheDocument();
  });

  it("shows exact evidence page and coordinates", () => {
    render(<ForecastPanel report={report} />);

    const formula = screen.getAllByText("Формула и источники")[0];
    fireEvent.click(formula);
    expect(screen.getAllByText(/Evidence evidence-31-/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/стр\. 2/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\[10, 20, 110, 50\]/).length).toBeGreaterThan(0);
  });

  it("marks absent exact evidence instead of inventing a source", () => {
    const withoutEvidence: ForecastReport = {
      ...report,
      budget: {
        ...report.budget,
        lines: [{
          ...report.budget.lines[0],
          sources: [{ ...source, evidence_exact: false, evidence: [] }],
        }],
      },
    };
    render(<ForecastPanel report={withoutEvidence} />);
    expect(screen.getByText("Точное Evidence не привязано")).toBeInTheDocument();
  });

  it("warns about low confidence", () => {
    render(<ForecastPanel report={{ ...report, confidence: { ...report.confidence, score: 0.54, band: "low" } }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Низкая уверенность");
  });

  it("binds manual acknowledgement to the exact forecast id", () => {
    const acknowledge = vi.fn();
    render(<ForecastPanel report={report} onAcknowledge={acknowledge} />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить ознакомление" }));
    expect(acknowledge).toHaveBeenCalledWith("forecast-immutable-hash");
  });

  it("does not offer another acknowledgement for the same version", () => {
    render(<ForecastPanel report={report} acknowledgedForecastId={report.forecast_id} onAcknowledge={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Ознакомление зафиксировано" })).toBeDisabled();
  });

  it("renders safe loading and retry states", () => {
    const retry = vi.fn();
    const { rerender } = render(<ForecastPanel report={null} state="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Собираем факты");
    rerender(<ForecastPanel report={null} state="error" error="Данные недоступны" onReload={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("explains when no approved GPR is available", () => {
    render(<ForecastPanel report={{ ...report, schedule: { ...report.schedule, stages: [], predicted_finish: null } }} />);
    expect(screen.getByText("Нет утверждённой версии ГПР.")).toBeInTheDocument();
  });
});

describe("parseForecastReport", () => {
  it("accepts a fail-closed draft report", () => {
    expect(parseForecastReport(report)).toBe(report);
  });

  it("rejects a response that claims it can trigger actions", () => {
    expect(() => parseForecastReport({ ...report, can_trigger_actions: true })).toThrow("безопасности");
  });

  it("rejects an acknowledgement binding from another forecast", () => {
    expect(() => parseForecastReport({
      ...report,
      manual_confirmation: { ...report.manual_confirmation, binding: "another-forecast" },
    })).toThrow("не привязано");
  });
});
