export type ForecastEvidence = {
  evidence_id: string;
  source_version_id: string;
  confidence: number | null;
  verification: string;
  page?: number;
  coordinates?: number[];
};

export type ForecastSource = {
  entity_type: string;
  entity_id: number;
  fields: string[];
  state: string;
  evidence: ForecastEvidence[];
  evidence_exact: boolean;
};

export type ForecastRisk = {
  code: string;
  severity: "medium" | "high" | "critical";
  explanation: string;
  sources: ForecastSource[];
};

export type ForecastReport = {
  forecast_id: string;
  project_id: number;
  as_of: string;
  publication_state: "draft";
  advisory_only: true;
  can_trigger_actions: false;
  requires_human_confirmation: true;
  confidence: {
    score: number;
    band: "low" | "medium" | "high";
    formula: string;
    low_confidence_threshold: number;
  };
  schedule: {
    formula: string;
    predicted_finish: string | null;
    stages: Array<{
      id: number;
      title: string;
      planned_finish: string | null;
      predicted_finish: string | null;
      actual_progress: number;
      formula: string;
      formula_description: string;
      confidence: number;
      risks: string[];
      sources: ForecastSource[];
    }>;
  };
  budget: {
    formula: string;
    planned_total: string;
    forecast_total: string;
    variance: string;
    lines: Array<{
      id: number;
      description: string;
      currency: string;
      planned_amount: string;
      forecast_amount: string;
      variance: string;
      formula: string;
      confidence: number;
      sources: ForecastSource[];
    }>;
  };
  cash_flow: {
    formula: string;
    opening_balance: string;
    closing_balance: string;
    minimum_balance: string;
    cash_gap_date: string | null;
    events: Array<{
      id: number;
      title: string;
      date: string;
      direction: "inflow" | "outflow";
      amount: string;
      value_kind: "actual" | "planned";
      running_balance: string;
      confidence: number;
      risks: string[];
      sources: ForecastSource[];
    }>;
  };
  risks: ForecastRisk[];
  manual_confirmation: {
    binding: string;
    required_before: string[];
    reason: string;
    persistence_available: false;
  };
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function parseForecastReport(value: unknown): ForecastReport {
  if (!isRecord(value) || typeof value.forecast_id !== "string" || typeof value.project_id !== "number") {
    throw new Error("Неверный формат прогноза");
  }
  if (value.publication_state !== "draft" || value.advisory_only !== true || value.can_trigger_actions !== false) {
    throw new Error("Прогноз не прошёл проверку безопасности");
  }
  if (!isRecord(value.confidence) || typeof value.confidence.score !== "number") {
    throw new Error("В прогнозе нет оценки уверенности");
  }
  if (!isRecord(value.schedule) || !Array.isArray(value.schedule.stages)) {
    throw new Error("В прогнозе нет расчёта сроков");
  }
  if (!isRecord(value.budget) || !Array.isArray(value.budget.lines)) {
    throw new Error("В прогнозе нет расчёта бюджета");
  }
  if (!isRecord(value.cash_flow) || !Array.isArray(value.cash_flow.events) || !Array.isArray(value.risks)) {
    throw new Error("В прогнозе нет расчёта ДДС");
  }
  if (!isRecord(value.manual_confirmation) || value.manual_confirmation.binding !== value.forecast_id) {
    throw new Error("Ручное подтверждение не привязано к версии прогноза");
  }
  return value as ForecastReport;
}
