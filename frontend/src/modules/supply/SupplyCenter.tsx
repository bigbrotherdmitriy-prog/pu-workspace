import { SupplyChainPanel } from "./SupplyChainPanel";
import { useSupplyCases } from "./useSupplyCases";

type Props = {
  projectId: number | null;
  enabled?: boolean;
  canEdit: boolean;
  canManage: boolean;
};

export function SupplyCenter({ projectId, enabled = true, canEdit, canManage }: Props) {
  const controller = useSupplyCases(projectId, enabled, canManage);

  return <section className="supply-center" aria-label="Снабжение и приёмка">
    <div className="supply-center__head">
      <div>
        <span className="supply-chain__eyebrow">СНАБЖЕНИЕ · ДОКАЗАТЕЛЬНАЯ ЦЕПОЧКА</span>
        <h2>Заявка → заказ → поставка → акт</h2>
        <p>Каждое решение привязано к версии записи. Внешние заказы, подписи и платежи отсюда не выполняются.</p>
      </div>
      <button type="button" className="secondary" disabled={controller.loadState === "loading"}
        onClick={() => void controller.reload()}>Обновить</button>
    </div>

    {controller.loadState === "loading" && <p role="status">Загружаю цепочки снабжения…</p>}
    {controller.loadState === "error" && <div role="alert" className="supply-center__message supply-center__message--error">
      <p>{controller.error}</p>
      <button type="button" onClick={() => void controller.reload()}>Повторить</button>
    </div>}
    {controller.loadState === "empty" && <div className="supply-center__empty">
      <strong>Цепочек снабжения пока нет</strong>
      <p>Они появятся после подтверждения заявки, связанной с договором, этапом ГПР, задачей и точным доказательством.</p>
    </div>}
    {controller.mutationMessage && <p role={controller.mutationState === "saved" ? "status" : "alert"}
      className={`supply-center__message supply-center__message--${controller.mutationState}`}>
      {controller.mutationMessage}
    </p>}
    <div className="supply-center__items">
      {controller.items.map((item) => <SupplyChainPanel
        key={item.id}
        item={item}
        canEdit={canEdit}
        canManage={canManage}
        busy={controller.busyId === item.id}
        onAction={(action, current) => void controller.runAction(action, current)}
      />)}
    </div>
  </section>;
}
