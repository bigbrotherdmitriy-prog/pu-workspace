import type { ReactNode } from "react";

type ContractKind = "prime_reference" | "customer" | "revenue_subcontract" | "downstream_subcontract" | "supply";

type Props = {
  collapsed: boolean;
  number: string;
  title: string;
  counterparty: string;
  kind: ContractKind;
  parentContractId: number;
  amount: string;
  advanceAmount: string;
  retentionPercent: string;
  signedAt: string;
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
  number,
  title,
  counterparty,
  kind,
  parentContractId,
  amount,
  advanceAmount,
  retentionPercent,
  signedAt,
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
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page contracts-page">
        <section className="card contract-create">
          <div>
            <h2>Добавить договор</h2>
            <p>Основной договор, субподряд или поставка становятся юридическим якорем ГПР, платежей и ДДС.</p>
          </div>
          <div className="contract-form">
            <input value={number} onChange={(event) => onNumberChange(event.target.value)} placeholder="Номер договора" />
            <input value={title} onChange={(event) => onTitleChange(event.target.value)} placeholder="Название" />
            <input value={counterparty} onChange={(event) => onCounterpartyChange(event.target.value)} placeholder="Контрагент" />
            <select value={kind} onChange={(event) => onKindChange(event.target.value as ContractKind)}>
              <option value="prime_reference">Генподрядный договор — только контекст</option>
              <option value="revenue_subcontract">Наш субподрядный договор — доходы, ГПР, бюджет и ДДС</option>
              <option value="customer">Прямой договор с заказчиком — доходы, ГПР, бюджет и ДДС</option>
              <option value="downstream_subcontract">Договор с субподрядчиком / субсубподрядчиком — расходы</option>
              <option value="supply">Договор поставки — расходы</option>
            </select>
            {!["prime_reference", "customer"].includes(kind) && <select value={parentContractId} onChange={(event) => onParentContractIdChange(Number(event.target.value))}>
              <option value={0}>{kind === "revenue_subcontract" ? "Выберите генподрядный договор" : "Выберите непосредственный вышестоящий договор"}</option>
              {contracts.filter((item) => kind === "revenue_subcontract"
                ? item.contract_kind === "prime_reference"
                : ["customer", "revenue_subcontract", "downstream_subcontract"].includes(item.contract_kind || "customer")
              ).map((item) => <option value={item.id} key={item.id}>↳ {item.number} — {item.title}</option>)}
            </select>}
            <input type="number" min="0" step="0.01" value={amount} onChange={(event) => onAmountChange(event.target.value)} placeholder="Сумма договора, ₽" />
            <input type="number" min="0" step="0.01" value={advanceAmount} onChange={(event) => onAdvanceAmountChange(event.target.value)} placeholder="Аванс, ₽" />
            <input type="number" min="0" max="100" step="0.01" value={retentionPercent} onChange={(event) => onRetentionPercentChange(event.target.value)} placeholder="Удержание, %" />
            <label>Дата подписания<input aria-label="Дата подписания договора" type="date" value={signedAt} onChange={(event) => onSignedAtChange(event.target.value)} /></label>
            <button disabled={!number.trim() || !title.trim() || (!["prime_reference", "customer"].includes(kind) && !parentContractId)} onClick={onCreate}>Добавить</button>
          </div>
        </section>
        {children}
      </div>
    </section>
  );
}
