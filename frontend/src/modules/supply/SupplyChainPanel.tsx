import {
  availableSupplyActions,
  supplyActionLabels,
  supplyActiveStep,
  supplyStatusLabels,
  type SupplyAction,
  type SupplyCaseView,
} from "./supplyReadModel";
import "./SupplyChainPanel.css";

interface Props {
  item: SupplyCaseView;
  canManage: boolean;
  canEdit?: boolean;
  busy?: boolean;
  onAction: (action: SupplyAction, item: SupplyCaseView) => void;
}

const steps = ["Заявка", "Согласование", "Заказ", "Поставка", "Акт"];

export function SupplyChainPanel({ item, canManage, canEdit = canManage, busy = false, onAction }: Props) {
  const actions = availableSupplyActions(item, canManage, canEdit);
  const activeStep = supplyActiveStep[item.status];
  return <section className="supply-chain" aria-label="Закупка, поставка и акт">
    <header>
      <div>
        <span className="supply-chain__eyebrow">Версия {item.recordVersion}</span>
        <h3>{item.title}</h3>
        <p>{item.supplier}</p>
      </div>
      <span className={`supply-chain__status supply-chain__status--${item.status}`}>{supplyStatusLabels[item.status]}</span>
    </header>

    <ol className="supply-chain__steps">
      {steps.map((step, index) => <li key={step} className={index < activeStep ? "complete" : index === activeStep ? "active" : ""}
        aria-current={index === activeStep ? "step" : undefined}>{step}</li>)}
    </ol>

    <dl className="supply-chain__quantities">
      <div><dt>Запрошено</dt><dd>{item.requestedQuantity} {item.unit}</dd></div>
      <div><dt>Заказано</dt><dd>{item.orderedQuantity} {item.unit}</dd></div>
      <div><dt>Поставлено</dt><dd>{item.deliveredQuantity} {item.unit}</dd></div>
      <div><dt>Принято</dt><dd>{item.acceptedQuantity} {item.unit}</dd></div>
    </dl>

    <details className="supply-chain__evidence">
      <summary>Связи и доказательства</summary>
      <p>Договор #{item.contractId} · ГПР #{item.scheduleBaselineId} v{item.scheduleBaselineVersion} · этап #{item.scheduleItemId}</p>
      <p>Задача #{item.taskId} · документ v{item.documentVersionId} · evidence r{item.evidenceRevision}</p>
    </details>

    {item.reviewState === "needs_review" && <p role="alert" className="supply-chain__warning">
      Низкая уверенность: продолжение возможно только после ручной проверки.
    </p>}
    {item.status === "delivery_discrepancy" && <p role="alert" className="supply-chain__warning">
      Обнаружено расхождение поставки. Акт заблокирован до решения менеджера.
    </p>}

    <div className="supply-chain__actions">
      {actions.map((action) => <button key={action} type="button" disabled={busy} onClick={() => onAction(action, item)}>
        {supplyActionLabels[action]}
      </button>)}
      {actions.length === 0 && <span>Действий сейчас нет</span>}
    </div>
    <small>Система не размещает заказ, не подписывает акт и не проводит оплату автоматически.</small>
  </section>;
}
