export type FinanceOverview = {
  summary: {
    budget_planned: number;
    budget_committed: number;
    budget_actual: number;
    budget_forecast: number;
    budget_variance: number;
    cash_balance_forecast: number;
    cash_gap: number;
    cash_gap_date?: string;
    delayed_schedule: number;
    late_procurement: number;
    acts_pending: number;
    pending_payments: number;
    unlinked_invoices: number;
  };
  baselines: {
    id: number;
    contract_id?: number;
    name: string;
    version: number;
    status: string;
    note?: string;
  }[];
  schedule: {
    id: number;
    baseline_id: number;
    title: string;
    planned_finish?: string;
    planned_progress: number;
    actual_progress: number;
    status: string;
  }[];
  budget: {
    id: number;
    contract_id?: number;
    category: string;
    description: string;
    planned_amount: number;
    actual_amount: number;
    committed_amount: number;
    forecast_amount: number;
    currency: string;
    status: string;
  }[];
  cash_flow: {
    id: number;
    contract_id?: number;
    schedule_item_id?: number;
    budget_line_id?: number;
    source_document_id?: number;
    direction: string;
    title: string;
    planned_date: string;
    planned_amount: number;
    actual_amount: number;
    status: string;
  }[];
  procurement: {
    id: number;
    contract_id?: number;
    title: string;
    supplier?: string;
    stage: string;
    planned_delivery?: string;
    planned_amount: number;
    actual_amount: number;
  }[];
  acts: {
    id: number;
    contract_id?: number;
    number: string;
    title: string;
    act_date?: string;
    amount: number;
    status: string;
  }[];
};

export type FinanceDocumentCandidate = {
  document_id: number;
  name: string;
  source: string;
  kind: "schedule" | "budget" | "invoice" | "cash-flow" | "act";
  score: number;
  reasons: string[];
  hints: { amount?: string; date?: string; number?: string };
  already_linked: boolean;
  originals_changed: boolean;
};

export type FinanceStructuredRow = {
  source_row: number;
  title: string;
  category: string;
  planned_start?: string;
  planned_finish?: string;
  planned_date?: string;
  amount?: string;
  counterparty?: string;
  direction?: string;
  progress: number;
  issues: string[];
  importable: boolean;
};

export type FinanceStructuredPreview = {
  document_id: number;
  name: string;
  kind: "schedule" | "budget" | "cash-flow";
  mapping: Record<string, string>;
  rows: FinanceStructuredRow[];
  issues: string[];
  truncated: boolean;
};
