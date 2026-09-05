import { useState, type FormEvent } from "react";
import type { SupplyAction, SupplyCaseView, SupplyEvidenceOption } from "./supplyReadModel";

export type SupplyActionFields = Record<string, unknown>;

type Props = {
  action: SupplyAction;
  item: SupplyCaseView;
  evidence: SupplyEvidenceOption[];
  evidenceLoading: boolean;
  onCancel: () => void;
  onSubmit: (fields: SupplyActionFields) => void;
};

const quantityPattern = /^\d{1,15}(?:\.\d{1,3})?$/;
const moneyPattern = /^\d{1,15}(?:\.\d{1,2})?$/;
const needsEvidence = new Set<SupplyAction>(["record_order", "record_delivery", "propose_act", "propose_dds"]);

function positive(value: string, pattern: RegExp): boolean {
  return pattern.test(value) && Number(value) > 0;
}

function evidencePayload(option: SupplyEvidenceOption) {
  return {
    evidence_id: option.evidenceId,
    evidence_revision: option.evidenceRevision,
    source_version_id: option.sourceVersionId,
    document_version_id: option.documentVersionId,
  };
}

export function SupplyActionForm({ action, item, evidence, evidenceLoading, onCancel, onSubmit }: Props) {
  const [decision, setDecision] = useState(action === "review" ? "confirm" : "accept_recorded_quantity");
  const [quantity, setQuantity] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [plannedDate, setPlannedDate] = useState("");
  const [budgetLineId, setBudgetLineId] = useState("");
  const [ddsAmount, setDdsAmount] = useState(
    (Number(item.orderedQuantity) * Number(item.unitPrice)).toFixed(2),
  );
  const [correctedTitle, setCorrectedTitle] = useState("");
  const [correctedSupplier, setCorrectedSupplier] = useState("");
  const [reference, setReference] = useState("");
  const [actNumber, setActNumber] = useState("");
  const [evidenceId, setEvidenceId] = useState("");
  const [discrepancyCode, setDiscrepancyCode] = useState("");
  const [discrepancyNote, setDiscrepancyNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const selected = evidence.find((option) => option.evidenceId === evidenceId);

  function submit(event: FormEvent) {
    event.preventDefault();
    const fields: SupplyActionFields = {};
    if (action === "review") {
      fields.decision = decision;
      if (quantity) {
        if (!positive(quantity, quantityPattern)) return setError("Количество: максимум 3 знака после точки.");
        fields.corrected_quantity = quantity;
      }
      if (unitPrice) {
        if (!moneyPattern.test(unitPrice)) return setError("Цена: максимум 2 знака после точки.");
        fields.corrected_unit_price = unitPrice;
      }
      if (correctedTitle.trim()) fields.corrected_title = correctedTitle.trim();
      if (correctedSupplier.trim()) fields.corrected_supplier = correctedSupplier.trim();
    }
    if (action === "prepare_order") {
      if (!positive(quantity, quantityPattern)) return setError("Количество: максимум 3 знака после точки.");
      if (reference.trim().length < 2) return setError("Укажите номер или ссылку заказа.");
      fields.ordered_quantity = quantity;
      fields.order_reference = reference.trim();
    }
    if (action === "record_delivery" || action === "propose_act") {
      if (!positive(quantity, quantityPattern)) return setError("Количество: максимум 3 знака после точки.");
      fields[action === "record_delivery" ? "delivered_quantity" : "accepted_quantity"] = quantity;
    }
    if (action === "propose_act") {
      if (!actNumber.trim()) return setError("Укажите номер акта.");
      fields.act_number = actNumber.trim();
    }
    if (action === "resolve_discrepancy") fields.decision = decision;
    if (action === "propose_dds") {
      if (!moneyPattern.test(ddsAmount) || Number(ddsAmount) <= 0) {
        return setError("Сумма ДДС: максимум 2 знака после точки.");
      }
      if (!plannedDate) return setError("Укажите плановую дату.");
      if (!/^\d+$/.test(budgetLineId) || Number(budgetLineId) <= 0) {
        return setError("Укажите подтверждённую строку бюджета.");
      }
      fields.contract_id = item.contractId;
      fields.schedule_item_id = item.scheduleItemId;
      fields.budget_line_id = Number(budgetLineId);
      fields.planned_date = plannedDate;
      fields.amount = ddsAmount;
      fields.currency = item.currency;
      fields.evidence_assessment_version = selected?.assessmentVersion;
    }
    if (action === "record_delivery") {
      if (discrepancyCode && discrepancyNote.trim().length < 3) return setError("Опишите расхождение.");
      if (!discrepancyCode && discrepancyNote) return setError("Выберите тип расхождения.");
      if (discrepancyCode) {
        fields.discrepancy_code = discrepancyCode;
        fields.discrepancy_note = discrepancyNote.trim();
      }
    }
    if (needsEvidence.has(action)) {
      if (!selected || selected.verification !== "verified") {
        return setError("Выберите проверенное точное доказательство текущей версии.");
      }
      fields.evidence = evidencePayload(selected);
    }
    setError(null);
    onSubmit(fields);
  }

  return <form className="supply-action-form" aria-label="Форма действия снабжения" onSubmit={submit}>
    <h4>Действие для «{item.title}» · версия {item.recordVersion}</h4>
    {action === "review" && <>
      <label>Решение<select value={decision} onChange={(event) => setDecision(event.target.value)}>
        <option value="confirm">Подтвердить после проверки</option><option value="reject">Отклонить</option>
      </select></label>
      {decision === "confirm" && <><label>Исправленное название (необязательно)<input value={correctedTitle} onChange={(event) => setCorrectedTitle(event.target.value)} /></label>
        <label>Исправленный поставщик (необязательно)<input value={correctedSupplier} onChange={(event) => setCorrectedSupplier(event.target.value)} /></label>
        <label>Исправленное количество (необязательно)<input value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Исправленная цена (необязательно)<input value={unitPrice} onChange={(event) => setUnitPrice(event.target.value)} /></label></>}
    </>}
    {action === "prepare_order" && <><label>Количество<input value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
      <label>Номер заказа<input value={reference} onChange={(event) => setReference(event.target.value)} required /></label></>}
    {(action === "record_delivery" || action === "propose_act") && <label>Количество<input value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>}
    {action === "propose_act" && <label>Номер акта<input value={actNumber} onChange={(event) => setActNumber(event.target.value)} required /></label>}
    {action === "propose_dds" && <>
      <p>Договор #{item.contractId} · этап ГПР #{item.scheduleItemId} · валюта {item.currency}</p>
      <label>Сумма предложения ДДС<input value={ddsAmount} onChange={(event) => setDdsAmount(event.target.value)} required /></label>
      <label>Плановая дата<input type="date" value={plannedDate} onChange={(event) => setPlannedDate(event.target.value)} required /></label>
      <label>Подтверждённая строка бюджета #<input inputMode="numeric" value={budgetLineId} onChange={(event) => setBudgetLineId(event.target.value)} required /></label>
      <p>Будет создано только предложение. Оплата и проводка не выполняются.</p>
    </>}
    {action === "record_delivery" && <><label>Расхождение<select value={discrepancyCode} onChange={(event) => setDiscrepancyCode(event.target.value)}>
      <option value="">Нет</option><option value="quantity">Количество</option><option value="quality">Качество</option>
      <option value="damage">Повреждение</option><option value="documents">Документы</option><option value="other">Другое</option>
    </select></label>{discrepancyCode && <label>Описание расхождения<textarea value={discrepancyNote} onChange={(event) => setDiscrepancyNote(event.target.value)} required /></label>}</>}
    {action === "resolve_discrepancy" && <label>Решение<select value={decision} onChange={(event) => setDecision(event.target.value)}>
      <option value="accept_recorded_quantity">Принять зафиксированное количество</option>
      <option value="return_to_delivery">Вернуть на уточнение поставки</option>
    </select></label>}
    {needsEvidence.has(action) && <label>Точное доказательство<select value={evidenceId} onChange={(event) => setEvidenceId(event.target.value)} disabled={evidenceLoading} required>
      <option value="">{evidenceLoading ? "Загрузка…" : "Выберите доказательство"}</option>
      {evidence.map((option) => <option key={option.evidenceId} value={option.evidenceId} disabled={option.verification !== "verified"}>
        Документ v{option.documentVersionId} · evidence {option.evidenceId.slice(0, 8)} · {option.verification === "verified" ? "проверено" : "не проверено"}
      </option>)}
    </select></label>}
    {error && <p role="alert">{error}</p>}
    <p>Доказательство выбирается заново для этого шага. Имя файла не считается доказательством.</p>
    <div className="supply-action-form__buttons"><button type="submit">Сохранить решение</button><button type="button" onClick={onCancel}>Отмена</button></div>
  </form>;
}
