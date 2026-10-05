import type { ReactNode } from "react";
import { contractMoney, vatLabel, type VatMode } from "./contractCommercial";
import "./contract-details.css";

type DetailsContract = {
  id: number; number: string; title: string; record_version: number; counterparty?: string | null;
  amount?: number | string | null; advance_amount?: number | string | null; retention_percent?: number | string | null;
  signed_at?: string | null; performed_from?: string | null; performed_to?: string | null; warranty_until?: string | null;
  vat_mode?: VatMode; vat_rate?: string | null; notes?: string | null;
  version_history?: { id: number; sequence: number; event: string; changed_fields: string[]; occurred_at: string; snapshot?: Record<string, unknown> }[];
};

const dateLabel = (value?: string | null) => value ? value.slice(0, 10).split("-").reverse().join(".") : "Не указана";
const historyLabels: Record<string, string> = { number: "Номер", title: "Название", counterparty: "Контрагент", amount: "Сумма с НДС",
  advance_amount: "Аванс с НДС", retention_percent: "Удержание, %", vat_mode: "НДС", vat_rate: "Ставка НДС, %",
  signed_at: "Подписано", performed_from: "Исполнение с", performed_to: "Исполнение до", warranty_until: "Гарантия до",
  status: "Статус", notes: "Примечание", contract_kind: "Вид договора", parent_contract_id: "Вышестоящий договор",
  source_document_id: "Основной документ", linked_document_ids: "Документы комплекта" };

function snapshotValue(field: string, value: unknown, snapshot: Record<string, unknown>, currency: string): string {
  if (value == null) return "Не указано";
  if ((field === "amount" || field === "advance_amount") && (typeof value === "number" || typeof value === "string")) return contractMoney(value, currency);
  if (field === "vat_mode") return vatLabel(value as VatMode, snapshot.vat_rate as string | null);
  if (["signed_at", "performed_from", "performed_to", "warranty_until"].includes(field) && typeof value === "string") return dateLabel(value);
  if (Array.isArray(value)) return value.join(", ") || "Не указано";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

// Controller state belongs to App, above register/scheme mounts. Opening details
// never creates a second editor or replaces the version of an existing draft.
export function ContractDetails({ contract, children, currency = "RUB" }: { contract: DetailsContract; children: ReactNode; currency?: string }) {
  return <article className="card contract-card contract-details" aria-label={`Карточка договора ${contract.number}`}>
    <div className="contract-details-summary">
      <h3>{contract.number} — {contract.title}</h3>
      <p>{contract.counterparty || "Контрагент не указан"}</p>
      <small>Версия карточки: {contract.record_version} · снимков истории: {contract.version_history?.length || 0}</small>
      {contract.notes && <p>{contract.notes}</p>}
      <dl className="contract-commercial-summary">
        <div><dt>Сумма с НДС</dt><dd>{contract.amount == null ? "Не указана" : contractMoney(contract.amount, currency)}</dd></div>
        <div><dt>Аванс с НДС</dt><dd>{contract.advance_amount == null ? "Не указан" : contractMoney(contract.advance_amount, currency)}</dd></div>
        <div><dt>Удержание</dt><dd>{contract.retention_percent == null ? "Не указано" : `${contract.retention_percent}%`}</dd></div>
        <div><dt>НДС</dt><dd>{vatLabel(contract.vat_mode, contract.vat_rate)}</dd></div>
        <div><dt>Подписано</dt><dd>{dateLabel(contract.signed_at)}</dd></div>
        <div><dt>Исполнение с</dt><dd>{dateLabel(contract.performed_from)}</dd></div>
        <div><dt>Исполнение до</dt><dd>{dateLabel(contract.performed_to)}</dd></div>
        <div><dt>Гарантия до</dt><dd>{dateLabel(contract.warranty_until)}</dd></div>
      </dl>
      <p className="contract-period-notice" role="note">Серверная проверка финансового периода будет добавлена в V6-10b; сейчас даты договора не ограничивают финансовые операции.</p>
      <details className="contract-version-history">
        <summary>История договора</summary>
        {contract.version_history?.length ? <ol>{contract.version_history.map((version) => <li key={version.id}>
          <strong>Версия {version.sequence} · {version.event}</strong>
          <span>{version.changed_fields.join(", ") || "Исходное состояние"} · {new Date(version.occurred_at).toLocaleString("ru-RU")}</span>
          {version.snapshot && <details><summary>Значения версии {version.sequence}</summary>
            <table className="contract-history-values" aria-label={`Значения версии ${version.sequence}`}><tbody>
              {Object.entries(version.snapshot).map(([field, value]) => <tr key={field}>
                <th>{historyLabels[field] || field}</th><td>{snapshotValue(field, value, version.snapshot!, currency)}</td>
              </tr>)}
            </tbody></table>
          </details>}
        </li>)}</ol> : <p>Снимков истории пока нет.</p>}
      </details>
    </div>
    {children}
  </article>;
}

export type ContractTermsDraft = {
  vatMode: VatMode; vatRate: string; performedFrom: string; performedTo: string; warrantyUntil: string;
};

export function ContractTermsFields({ draft, onChange, disabled = false, creating = false, fieldErrors = {} }: {
  draft: ContractTermsDraft; onChange: (field: keyof ContractTermsDraft, value: string) => void;
  disabled?: boolean; creating?: boolean; fieldErrors?: Record<string, string>;
}) {
  return <>
    <label>НДС<select aria-label={creating ? "НДС нового договора" : "НДС договора"} disabled={disabled}
      aria-invalid={Boolean(fieldErrors.vat_mode)} value={draft.vatMode} onChange={(event) => onChange("vatMode", event.target.value)}>
      <option value="unspecified">Не указан</option><option value="none">Без НДС</option><option value="rate">Ставка НДС</option>
    </select></label>
    {draft.vatMode === "rate" && <label>Ставка НДС, %<input aria-label="Ставка НДС, %" disabled={disabled} type="number"
      aria-invalid={Boolean(fieldErrors.vat_rate)} min="0" max="100" step="0.01" value={draft.vatRate} onChange={(event) => onChange("vatRate", event.target.value)} /></label>}
    <label>Начало исполнения<input aria-label={creating ? "Начало исполнения нового договора" : "Начало исполнения договора"}
      aria-invalid={Boolean(fieldErrors.performed_from)} disabled={disabled} type="date" value={draft.performedFrom} onChange={(event) => onChange("performedFrom", event.target.value)} /></label>
    <label>Окончание исполнения<input aria-label={creating ? "Окончание исполнения нового договора" : "Окончание исполнения договора"}
      aria-invalid={Boolean(fieldErrors.performed_to)} disabled={disabled} type="date" value={draft.performedTo} onChange={(event) => onChange("performedTo", event.target.value)} /></label>
    <label>Гарантия до<input aria-label={creating ? "Гарантия нового договора до" : "Гарантия до"} disabled={disabled} type="date"
      aria-invalid={Boolean(fieldErrors.warranty_until)} value={draft.warrantyUntil} onChange={(event) => onChange("warrantyUntil", event.target.value)} /></label>
  </>;
}
