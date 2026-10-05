import type { ReactNode } from "react";
import { ContractTermsFields, type ContractTermsDraft } from "./ContractDetails";

type ContractKind = "prime_reference" | "customer" | "revenue_subcontract" | "downstream_subcontract" | "supply";

type Props = {
  collapsed: boolean;
  currency?: string;
  number: string;
  title: string;
  counterparty: string;
  kind: ContractKind;
  parentContractId: number;
  amount: string;
  advanceAmount: string;
  retentionPercent: string;
  signedAt: string;
  terms?: ContractTermsDraft;
  onTermsChange?: (field: keyof ContractTermsDraft, value: string) => void;
  feedback?: { message: string; fieldErrors?: Record<string, string> };
  contracts: { id: number; number: string; title: string; contract_kind?: string }[];
  onNumberChange: (value: string) => void;
  onTitleChange: (value: string) => void;
  onCounterpartyChange: (value: string) => void;
  onKindChange: (value: ContractKind) => void;
  onParentContractIdChange: (value: number) => void;
  onAmountChange: (value: string) => void;
  onAdvanceAmountChange: (value: string) => void;
  onRetentionPercentChange: (value: string) => void;
  onSignedAtChange: (value: string) => void;
  onCreate: () => void;
  children: ReactNode;
};

export function ContractsModule({
  collapsed,
  currency = "RUB",
  number,
  title,
  counterparty,
  kind,
  parentContractId,
  amount,
  advanceAmount,
  retentionPercent,
  signedAt,
  terms = { vatMode: "unspecified", vatRate: "", performedFrom: "", performedTo: "", warrantyUntil: "" },
  onTermsChange = () => {},
  feedback,
  contracts,
  onNumberChange,
  onTitleChange,
  onCounterpartyChange,
  onKindChange,
  onParentContractIdChange,
  onAmountChange,
  onAdvanceAmountChange,
  onRetentionPercentChange,
  onSignedAtChange,
  onCreate,
  children,
}: Props) {
  const currencyLabel = currency === "RUB" ? "₽" : currency;
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page contracts-page">
        <details className="card contract-create">
          <summary>Создать договор</summary>
          <p>Основной договор, субподряд или поставка становятся юридическим якорем ГПР, платежей и ДДС.</p>
          <div className="contract-form">
            {feedback?.message && <div className="form-operation-error" role="alert">{feedback.message}</div>}
            <input aria-label="Номер договора" value={number} onChange={(event) => onNumberChange(event.target.value)} placeholder="Номер договора" />
            <input aria-label="Название договора" value={title} onChange={(event) => onTitleChange(event.target.value)} placeholder="Название" />
            <input aria-label="Контрагент" value={counterparty} onChange={(event) => onCounterpartyChange(event.target.value)} placeholder="Контрагент" />
            <select aria-label="Вид договора" value={kind} onChange={(event) => onKindChange(event.target.value as ContractKind)}>
              <option value="prime_reference">Генподрядный договор — только контекст</option>
              <option value="revenue_subcontract">Наш субподрядный договор — доходы, ГПР, бюджет и ДДС</option>
              <option value="customer">Прямой договор с заказчиком — доходы, ГПР, бюджет и ДДС</option>
              <option value="downstream_subcontract">Договор с субподрядчиком / субсубподрядчиком — расходы</option>
              <option value="supply">Договор поставки — расходы</option>
            </select>
            {!["prime_reference", "customer"].includes(kind) && <select aria-label="Вышестоящий договор" value={parentContractId} onChange={(event) => onParentContractIdChange(Number(event.target.value))}>
              <option value={0}>{kind === "revenue_subcontract" ? "Выберите генподрядный договор" : "Выберите непосредственный вышестоящий договор"}</option>
              {contracts.filter((item) => kind === "revenue_subcontract"
                ? item.contract_kind === "prime_reference"
                : ["customer", "revenue_subcontract", "downstream_subcontract"].includes(item.contract_kind || "customer")
              ).map((item) => <option value={item.id} key={item.id}>↳ {item.number} — {item.title}</option>)}
            </select>}
            <input aria-label={`Сумма договора, ${currencyLabel}`} aria-invalid={Boolean(feedback?.fieldErrors?.amount)} type="number" min="0" step="0.01" value={amount} onChange={(event) => onAmountChange(event.target.value)} placeholder={`Сумма договора, ${currencyLabel}`} />
            <input aria-label={`Аванс, ${currencyLabel}`} aria-invalid={Boolean(feedback?.fieldErrors?.advance_amount)} type="number" min="0" step="0.01" value={advanceAmount} onChange={(event) => onAdvanceAmountChange(event.target.value)} placeholder={`Аванс, ${currencyLabel}`} />
            <input aria-label="Удержание, %" aria-invalid={Boolean(feedback?.fieldErrors?.retention_percent)} type="number" min="0" max="100" step="0.01" value={retentionPercent} onChange={(event) => onRetentionPercentChange(event.target.value)} placeholder="Удержание, %" />
            <label>Дата подписания<input aria-label="Дата подписания договора" type="date" value={signedAt} onChange={(event) => onSignedAtChange(event.target.value)} /></label>
            <ContractTermsFields creating draft={terms} onChange={onTermsChange} fieldErrors={feedback?.fieldErrors} />
            <p className="contract-period-notice">Сумма и аванс включают НДС. Серверная проверка финансового периода будет добавлена в V6-10b.</p>
            <button disabled={!number.trim() || !title.trim() || (!["prime_reference", "customer"].includes(kind) && !parentContractId)} onClick={onCreate}>Добавить договор</button>
          </div>
        </details>
        {children}
      </div>
    </section>
  );
}
