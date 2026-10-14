import type { ContractTermsDraft } from "./ContractDetails";
import type { SchemeDocument } from "./ContractScheme";
import type { VatMode, VatSnapshot } from "./contractCommercial";

/** Contract shapes shared by the shell and the contract screens (moved from App.tsx). */
export type ContractSourceCandidate = {
  document_id: number;
  name: string;
  source: string;
  mime_type?: string;
  score: number;
  reasons: string[];
  text_ready: boolean;
};
export type ContractFinancialCheck = {
  applied: string[];
  mismatches: { field: string; label: string; current: string; extracted: string; evidence?: string }[];
  terms: { amount?: string; advance_amount?: string; retention_percent?: string };
  sourceName?: string;
};
export type ContractRow = {
  id: number;
  record_version: number;
  number: string;
  title: string;
  counterparty?: string;
  contract_kind?: "prime_reference" | "customer" | "revenue_subcontract" | "downstream_subcontract" | "supply";
  parent_contract_id?: number;
  amount?: number | string | null;
  advance_amount?: number | string | null;
  retention_percent?: number | string | null;
  warranty_until?: string | null;
  signed_at?: string | null;
  performed_from?: string | null;
  performed_to?: string | null;
  vat_mode?: VatMode;
  vat_rate?: string | null;
  warnings?: { code: string; message: string }[];
  status: string;
  source_document_id?: number;
  notes?: string;
  linked_documents?: SchemeDocument[];
  version_history?: { id: number; sequence: number; event: string; changed_fields: string[]; occurred_at: string; snapshot?: Record<string, unknown> }[];
  analysis?: {
    source_ready: boolean;
    tasks: number;
    obligations: number;
    risks: number;
    decisions: number;
  };
  budget_proposals?: ContractBudgetProposal[];
};
export type ContractBudgetProposal = {
  id: number; contract_id: number; contract_record_version: number;
  operation: "create" | "revise"; amount: number | string; advance_amount?: number | string | null;
  retention_percent?: number | string | null; currency: string; description: string;
  vat_snapshot?: VatSnapshot | null;
  selected_cost_category_id?: number; source_document_id?: number;
  source_document_version_id?: number; source_document_sha256?: string;
  target_budget_line_id?: number; created_budget_line_id?: number;
  status: "proposed" | "confirmed" | "rejected" | "superseded";
};
export type ContractBudgetDraft = { amount: string; description: string; categoryId: number };
export type ContractEditDraft = ContractTermsDraft & {
  number: string; title: string; counterparty: string; amount: string;
  advanceAmount: string; retentionPercent: string; signedAt: string; status: string;
  expectedRecordVersion: number;
};
export type ContractEditFeedback = { message: string; fieldErrors?: Record<string, string>; conflict?: boolean; latest?: ContractRow; comparing?: boolean };
