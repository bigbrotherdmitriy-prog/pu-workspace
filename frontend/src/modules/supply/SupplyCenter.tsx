import { useEffect, useState } from "react";
import { SupplyActionForm, type SupplyActionFields } from "./SupplyActionForm";
import { SupplyChainPanel } from "./SupplyChainPanel";
import type { SupplyAction, SupplyCaseView } from "./supplyReadModel";
import { useSupplyCases } from "./useSupplyCases";

type Props = {
  projectId: number | null;
  enabled?: boolean;
  canEdit: boolean;
  canManage: boolean;
};

export function SupplyCenter({ projectId, enabled = true, canEdit, canManage }: Props) {
  const controller = useSupplyCases(projectId, enabled, canManage);
  const [form, setForm] = useState<{ action: SupplyAction; item: SupplyCaseView } | null>(null);
  const formActions = new Set<SupplyAction>([
    "review", "prepare_order", "record_order", "record_delivery", "resolve_discrepancy", "propose_act", "propose_dds",
  ]);
  const evidenceActions = new Set<SupplyAction>(["record_order", "record_delivery", "propose_act", "propose_dds"]);
  useEffect(() => setForm(null), [projectId, enabled]);

  function startAction(action: SupplyAction, item: SupplyCaseView) {
    if (!formActions.has(action)) {
      void controller.runAction(action, item);
      return;
    }
    setForm({ action, item });
    if (evidenceActions.has(action)) void controller.loadEvidence();
  }

  function submitForm(fields: SupplyActionFields) {
    if (!form) return;
    void controller.runAction(form.action, form.item, fields);
    setForm(null);
  }

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
    {controller.items.some((item) => item.decisionRequirements.length > 0) && <div
      className="supply-center__message" role="note">
      <strong>Требуются решения владельца или юриста</strong>
      <p>НДС, удержания, неизвестная валюта и источник курса не определяются автоматически.</p>
      <p>Автоматическая конвертация и оплата не выполняются.</p>
    </div>}
    {controller.mutationMessage && <p role={controller.mutationState === "saved" ? "status" : "alert"}
      className={`supply-center__message supply-center__message--${controller.mutationState}`}>
      {controller.mutationMessage}
    </p>}
    {form && <SupplyActionForm
      action={form.action}
      item={form.item}
      evidence={controller.evidence}
      evidenceLoading={controller.evidenceLoading}
      onCancel={() => setForm(null)}
      onSubmit={submitForm}
    />}
    <div className="supply-center__items">
      {controller.items.map((item) => <SupplyChainPanel
        key={item.id}
        item={item}
        canEdit={canEdit}
        canManage={canManage}
        busy={controller.busyId === item.id}
        onAction={startAction}
      />)}
    </div>
  </section>;
}
