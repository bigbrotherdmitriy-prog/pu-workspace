import type { ReactNode } from "react";

type Props = {
  collapsed: boolean;
  number: string;
  title: string;
  counterparty: string;
  onNumberChange: (value: string) => void;
  onTitleChange: (value: string) => void;
  onCounterpartyChange: (value: string) => void;
  onCreate: () => void;
  children: ReactNode;
};

export function ContractsModule({
  collapsed,
  number,
  title,
  counterparty,
  onNumberChange,
  onTitleChange,
  onCounterpartyChange,
  onCreate,
  children,
}: Props) {
  return (
    <section className={`module-overlay ${collapsed ? "collapsed" : ""}`}>
      <div className="module-page contracts-page">
        <section className="card contract-create">
          <div>
            <h2>Добавить договор</h2>
            <p>Договор становится юридическим якорем документов, задач и решений проекта.</p>
          </div>
          <div className="contract-form">
            <input value={number} onChange={(event) => onNumberChange(event.target.value)} placeholder="Номер договора" />
            <input value={title} onChange={(event) => onTitleChange(event.target.value)} placeholder="Название" />
            <input value={counterparty} onChange={(event) => onCounterpartyChange(event.target.value)} placeholder="Контрагент" />
            <button disabled={!number.trim() || !title.trim()} onClick={onCreate}>Добавить</button>
          </div>
        </section>
        {children}
      </div>
    </section>
  );
}
