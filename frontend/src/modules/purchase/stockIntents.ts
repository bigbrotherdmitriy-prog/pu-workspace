export type StockKind = "receipt" | "writeoff" | "storno";
export type StockIntent = Readonly<{
  version: 1; userId: number; projectId: number; itemId: number; kind: StockKind;
  operationKey: string; qty: string; unit: string; entryId?: number; reversesId?: number; source?: string | null;
}>;
const positiveId = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
export function stockQuantity(raw: unknown): string {
  if (typeof raw !== "string") throw new Error("Укажите количество числом");
  const value = raw.trim().replace(",", ".");
  if (!/^\d{1,15}(?:\.\d{1,3})?$/.test(value)) throw new Error("Количество: до 3 знаков после запятой, без округления");
  const [integer, fraction = ""] = value.split(".");
  const canonical = `${integer.replace(/^0+(?=\d)/, "")}.${fraction.padEnd(3, "0")}`;
  if (/^0\.0+$/.test(canonical)) throw new Error("Количество должно быть больше нуля");
  return canonical;
}
export function stockIntentKey(userId: number, projectId: number) { return `stock-intent:${userId}:${projectId}`; }
export function checkedStockIntent(value: unknown, userId: number, projectId: number): StockIntent {
  if (!value || typeof value !== "object") throw new Error("Некорректный сохранённый повтор");
  const v = value as StockIntent;
  if (v.version !== 1 || v.userId !== userId || v.projectId !== projectId || !positiveId(v.userId) || !positiveId(v.projectId) || !positiveId(v.itemId)
    || !["receipt", "writeoff", "storno"].includes(v.kind) || typeof v.operationKey !== "string" || !v.operationKey.trim() || v.operationKey.trim() !== v.operationKey || v.operationKey.length > 160
    || typeof v.unit !== "string" || !v.unit.trim() || v.unit.trim() !== v.unit || v.unit.length > 40 || stockQuantity(v.qty) !== v.qty
    || (v.kind === "writeoff" && !positiveId(v.entryId)) || (v.kind === "storno" && !positiveId(v.reversesId))
    || (v.source != null && (typeof v.source !== "string" || v.source.length > 500))) throw new Error("Сохранённое движение не принадлежит этой учётной записи и проекту или повреждено");
  return Object.freeze({ ...v });
}
export function stockRequest(intent: StockIntent): { path: string; body: Record<string, unknown> } {
  const root = `/projects/${intent.projectId}/purchase`;
  if (intent.kind === "storno") return { path: `${root}/writeoffs/${intent.reversesId}/storno`, body: { operation_key: intent.operationKey } };
  const base = { operation_key: intent.operationKey, qty: intent.qty, unit: intent.unit };
  return intent.kind === "receipt"
    ? { path: `${root}/${intent.itemId}/physical-receipts`, body: { ...base, source_document_ref: intent.source || null } }
    : { path: `${root}/${intent.itemId}/writeoffs`, body: { ...base, work_log_entry_id: intent.entryId } };
}
export function requireStockAcknowledgement(intent: StockIntent, result: unknown): void {
  const r = result as Record<string, unknown> | null;
  if (!r || !positiveId(r.id) || r.project_id !== intent.projectId || r.purchase_item_id !== intent.itemId || r.operation_key !== intent.operationKey
    || stockQuantity(r.qty) !== intent.qty || r.unit !== intent.unit
    || (intent.kind !== "receipt" && r.applied !== true)
    || (intent.kind === "writeoff" && (r.work_log_entry_id !== intent.entryId || r.reverses_id != null))
    || (intent.kind === "storno" && r.reverses_id !== intent.reversesId)
    || (intent.kind === "receipt" && (r.source_document_ref || null) !== (intent.source || null))) {
    throw new Error("Не подтверждено нужное движение. Повторите исходную операцию; новое движение не создавайте.");
  }
}
