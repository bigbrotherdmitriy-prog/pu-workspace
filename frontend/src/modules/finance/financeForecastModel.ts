export type ForecastMoney = {
  planned: number;
  committed: number;
  actual: number;
  forecast: number;
  variance: number;
};

export type FinanceForecastRollup = ForecastMoney & {
  key: string;
  label: string;
  contractId: number | null;
  scheduleItemId: number | null;
  level?: number;
  isSummary?: boolean;
};

export type FinanceForecastView = {
  projectId: number;
  currency: "RUB";
  totals: ForecastMoney;
  cashGap: number;
  cashGapDate: string | null;
  reliability: "reliable" | "decision_required" | "unreliable";
  reliabilityMessage: string;
  decisions: Array<{ code: string; decisionBy: "OWNER" | "LEGAL"; message: string }>;
  contracts: FinanceForecastRollup[];
  wbs: FinanceForecastRollup[];
  externalEffects: { paymentCreated: false; postingCreated: false; automaticConversion: false };
};

type RecordValue = Record<string, unknown>;
type ScheduleRow = {
  id: number;
  baselineId: number;
  title: string;
  parentId: number | null;
  level: number;
  isSummary: boolean;
};
type BudgetRow = ForecastMoney & { contractId: number | null; scheduleItemId: number | null };

const object = (value: unknown): value is RecordValue => typeof value === "object" && value !== null && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const optionalId = (value: unknown): value is number | null | undefined => value === null || value === undefined || integer(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const date = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
const money = (value: unknown, field: string, allowNegative = false): number => {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER || (!allowNegative && value < 0)) {
    throw new Error(`invalid_${field}`);
  }
  return value;
};
const close = (left: number, right: number) => Math.abs(left - right) < 0.005;
const emptyMoney = (): ForecastMoney => ({ planned: 0, committed: 0, actual: 0, forecast: 0, variance: 0 });
const addMoney = (target: ForecastMoney, row: ForecastMoney) => {
  target.planned += row.planned;
  target.committed += row.committed;
  target.actual += row.actual;
  target.forecast += row.forecast;
  target.variance = target.forecast - target.planned;
};

function parseSchedule(raw: unknown[]): ScheduleRow[] {
  const seen = new Set<number>();
  const rows = raw.map((value) => {
    if (!object(value) || !integer(value.id) || seen.has(value.id) || !integer(value.baseline_id) || !text(value.title)
      || !optionalId(value.wbs_parent_id)
      || !(value.wbs_level === undefined || typeof value.wbs_level === "number" && Number.isSafeInteger(value.wbs_level) && value.wbs_level >= 0 && value.wbs_level <= 4)
      || !(value.is_summary === undefined || typeof value.is_summary === "boolean")) throw new Error("invalid_schedule_row");
    seen.add(value.id);
    return { id: value.id, baselineId: value.baseline_id, title: value.title.trim(), parentId: value.wbs_parent_id ?? null,
      level: value.wbs_level ?? 0, isSummary: value.is_summary ?? false };
  });
  const byId = new Map(rows.map((row) => [row.id, row]));
  for (const row of rows) {
    if (row.parentId === null) {
      if (row.level !== 0) throw new Error("invalid_wbs_level");
      continue;
    }
    const parent = byId.get(row.parentId);
    if (!parent || !parent.isSummary || parent.level + 1 !== row.level) throw new Error("invalid_wbs_parent");
    const visited = new Set([row.id]);
    let cursor: ScheduleRow | undefined = parent;
    while (cursor) {
      if (visited.has(cursor.id)) throw new Error("wbs_cycle");
      visited.add(cursor.id);
      cursor = cursor.parentId === null ? undefined : byId.get(cursor.parentId);
    }
  }
  return rows;
}

function parseBudget(raw: unknown[]): BudgetRow[] {
  const accepted = new Set(["approved", "active", "closed"]);
  const seen = new Set<number>();
  return raw.flatMap((value) => {
    if (!object(value) || !integer(value.id) || !optionalId(value.contract_id) || !optionalId(value.schedule_item_id)
      || seen.has(value.id) || typeof value.status !== "string" || typeof value.currency !== "string") throw new Error("invalid_budget_row");
    seen.add(value.id);
    if (!accepted.has(value.status)) return [];
    if (value.currency !== "RUB") return [];
    const planned = money(value.planned_amount, "planned_amount");
    const committed = money(value.committed_amount, "committed_amount");
    const actual = money(value.actual_amount, "actual_amount");
    const forecast = money(value.forecast_amount ?? value.planned_amount, "forecast_amount");
    return [{ contractId: value.contract_id ?? null, scheduleItemId: value.schedule_item_id ?? null,
      planned, committed, actual, forecast, variance: forecast - planned }];
  });
}

function rollupRows(rows: BudgetRow[], keyOf: (row: BudgetRow) => string, labelOf: (key: string) => string): FinanceForecastRollup[] {
  const result = new Map<string, FinanceForecastRollup>();
  for (const row of rows) {
    const key = keyOf(row);
    const current = result.get(key) ?? { ...emptyMoney(), key, label: labelOf(key), contractId: row.contractId, scheduleItemId: row.scheduleItemId };
    addMoney(current, row);
    result.set(key, current);
  }
  return [...result.values()].sort((a, b) => a.key.localeCompare(b.key, "ru", { numeric: true }));
}

export function buildFinanceForecastView(raw: unknown, expectedProjectId: number): FinanceForecastView {
  if (!integer(expectedProjectId) || !object(raw) || !object(raw.readonly_view_scope)
    || raw.readonly_view_scope.project_id !== expectedProjectId || !object(raw.summary)
    || !Array.isArray(raw.baselines) || !Array.isArray(raw.schedule) || !Array.isArray(raw.budget)
    || !Array.isArray(raw.decision_requirements) || !object(raw.external_effects)) throw new Error("invalid_finance_overview");
  if (raw.external_effects.payment_created !== false || raw.external_effects.posting_created !== false
    || raw.external_effects.automatic_conversion !== false) throw new Error("unsafe_external_effects");

  const decisions = raw.decision_requirements.map((value) => {
    if (!object(value) || !text(value.code) || !text(value.message) || !["OWNER", "LEGAL"].includes(String(value.decision_by))) {
      throw new Error("invalid_decision_requirement");
    }
    return { code: value.code, decisionBy: value.decision_by as "OWNER" | "LEGAL", message: value.message };
  });
  const baselines = new Map<number, number | null>();
  for (const value of raw.baselines) {
    if (!object(value) || !integer(value.id) || !optionalId(value.contract_id) || baselines.has(value.id)) throw new Error("invalid_baseline");
    baselines.set(value.id, value.contract_id ?? null);
  }
  const schedule = parseSchedule(raw.schedule);
  for (const row of schedule) if (!baselines.has(row.baselineId)) throw new Error("unknown_baseline");
  const scheduleById = new Map(schedule.map((row) => [row.id, row]));
  const budget = parseBudget(raw.budget);
  for (const row of budget) {
    if (row.scheduleItemId !== null && !scheduleById.has(row.scheduleItemId)) throw new Error("unknown_schedule_item");
    if (row.scheduleItemId !== null) {
      const scheduleRow = scheduleById.get(row.scheduleItemId)!;
      if (scheduleRow.isSummary) throw new Error("summary_budget_link_forbidden");
      const scheduleContract = baselines.get(scheduleRow.baselineId) ?? null;
      if (row.contractId !== scheduleContract) throw new Error("budget_contract_schedule_mismatch");
    }
  }

  const totals = {
    planned: money(raw.summary.budget_planned, "summary_planned"),
    committed: money(raw.summary.budget_committed, "summary_committed"),
    actual: money(raw.summary.budget_actual, "summary_actual"),
    forecast: money(raw.summary.budget_forecast, "summary_forecast"),
    variance: money(raw.summary.budget_variance, "summary_variance", true),
  };
  if (!close(totals.variance, totals.forecast - totals.planned)) throw new Error("invalid_summary_variance");
  const calculated = emptyMoney();
  budget.forEach((row) => addMoney(calculated, row));
  if (!["planned", "committed", "actual", "forecast", "variance"].every((field) => close(calculated[field as keyof ForecastMoney], totals[field as keyof ForecastMoney]))) {
    throw new Error("finance_summary_mismatch");
  }

  const contractRollups = rollupRows(budget, (row) => String(row.contractId ?? "unlinked"), (key) => key === "unlinked" ? "Без договора" : `Договор #${key}`);
  const directBySchedule = new Map<number, BudgetRow[]>();
  for (const row of budget) if (row.scheduleItemId !== null) directBySchedule.set(row.scheduleItemId, [...(directBySchedule.get(row.scheduleItemId) ?? []), row]);
  const descendants = (id: number): BudgetRow[] => {
    const childIds = schedule.filter((row) => row.parentId === id).map((row) => row.id);
    return [...(directBySchedule.get(id) ?? []), ...childIds.flatMap(descendants)];
  };
  const wbs = schedule.map((row) => {
    const aggregate = emptyMoney();
    descendants(row.id).forEach((item) => addMoney(aggregate, item));
    return { ...aggregate, key: String(row.id), label: row.title, contractId: baselines.get(row.baselineId) ?? null,
      scheduleItemId: row.id, level: row.level, isSummary: row.isSummary };
  });
  const reliableFlag = raw.summary.financial_totals_reliable;
  if (typeof reliableFlag !== "boolean") throw new Error("missing_reliability_state");
  const excluded = money(raw.summary.excluded_currency_rows ?? 0, "excluded_currency_rows");
  const reliability = decisions.length ? "decision_required" : reliableFlag && excluded === 0 ? "reliable" : "unreliable";
  const cashGap = money(raw.summary.cash_gap, "cash_gap", true);
  const cashGapDate = raw.summary.cash_gap_date === undefined || raw.summary.cash_gap_date === null ? null
    : date(raw.summary.cash_gap_date) ? raw.summary.cash_gap_date : (() => { throw new Error("invalid_cash_gap_date"); })();
  if (cashGap > 0 || (cashGap < 0) !== (cashGapDate !== null)) throw new Error("invalid_cash_gap_basis");
  return {
    projectId: expectedProjectId,
    currency: "RUB",
    totals,
    cashGap,
    cashGapDate,
    reliability,
    reliabilityMessage: reliability === "reliable" ? "Итоги согласованы с подтверждёнными строками в RUB."
      : reliability === "decision_required" ? "Нужно решение владельца или юриста до использования итогов."
      : "Итоги неполны: есть исключённые валюты или неподтверждённая база.",
    decisions,
    contracts: contractRollups,
    wbs,
    externalEffects: { paymentCreated: false, postingCreated: false, automaticConversion: false },
  };
}
