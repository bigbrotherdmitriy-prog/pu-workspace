import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { getMeta, setMeta } from "../../offline/db";
import { captureOfflineSession, requireOfflineSession } from "../../offline/lifecycle";
import { checkedStockIntent, requireStockAcknowledgement, stockIntentKey, stockQuantity, stockRequest, type StockIntent } from "./stockIntents";

type Material = { id: number; name: string; unit: string | null };
type Entry = { id: number; work_date: string; work_name: string };
type Movement = { id: number; kind: "receipt" | "writeoff" | "storno"; qty: string; unit: string; created_at: string; reversed: boolean; work_log_entry_id: number | null; source_document_ref: string | null };
type History = { unit: string | null; balance: string | null; warning?: string | null; movements: Movement[]; has_more: boolean; limit: number };
const labels = { receipt: "Поступление", writeoff: "Списание", storno: "Сторно списания" };
export function PhysicalStockPanel({ projectId, userId, canManage, items }: { projectId: number; userId: number; canManage: boolean; items: Material[] }) {
  const scope = useMemo(() => ({ projectId, userId }), [projectId, userId]);
  const live = useRef<typeof scope | null>(scope); live.current = scope;
  const busyRef = useRef(false);
  const [itemId, setItemId] = useState(0);
  const [history, setHistory] = useState<History | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [kind, setKind] = useState<"receipt" | "writeoff">("receipt");
  const [qty, setQty] = useState(""); const [source, setSource] = useState(""); const [entryId, setEntryId] = useState(0);
  const [pending, setPending] = useState<StockIntent | null>(null);
  const [ready, setReady] = useState(false); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0); const [restoreTick, setRestoreTick] = useState(0);
  const item = items.find(row => row.id === itemId);
  useEffect(() => {
    let alive = true; busyRef.current = false; setBusy(false); setPending(null); setReady(false); setItemId(0); setHistory(null); setMessage(""); setError(""); setQty(""); setSource(""); setEntryId(0); setKind("receipt");
    if (userId > 0 && projectId > 0) getMeta<unknown>(stockIntentKey(userId, projectId)).then(saved => {
      if (!alive || live.current !== scope) return;
      if (saved != null) { const intent = checkedStockIntent(saved, userId, projectId); setPending(intent); setItemId(intent.itemId); }
      setReady(true);
    }).catch(reason => { if (alive && live.current === scope) setError(`Локальный повтор не прочитан: ${(reason as Error).message}. Новое движение не отправляется.`); });
    return () => { alive = false; if (live.current === scope) live.current = null; };
  }, [scope, projectId, userId, restoreTick]);
  useEffect(() => {
    let alive = true; setHistory(null); if (!itemId || projectId <= 0) return;
    api<History>(`/projects/${projectId}/purchase/${itemId}/stock-history?limit=25`).then(value => {
      if (alive && live.current === scope) setHistory(value);
    }).catch(reason => { if (alive && live.current === scope) setError((reason as Error).message); });
    return () => { alive = false; };
  }, [scope, projectId, itemId, tick]);
  useEffect(() => {
    let alive = true; setEntries([]); if (!canManage || projectId <= 0) return;
    api<{ entries: Entry[] }>(`/projects/${projectId}/work-log`).then(value => { if (alive && live.current === scope) setEntries(value.entries || []); }).catch(() => undefined);
    return () => { alive = false; };
  }, [scope, projectId, canManage]);
  async function send(intent: StockIntent) {
    if (busyRef.current || !canManage) return;
    const captured = scope; busyRef.current = true; setBusy(true); setError(""); setMessage(""); setPending(intent);
    let persisted = false;
    try {
      const session = captureOfflineSession(userId);
      // Persist before dispatch: a reload must retain the same body/idempotency key.
      await setMeta(stockIntentKey(userId, projectId), intent); persisted = true;
      requireOfflineSession(session); if (live.current !== captured) return;
      const request = stockRequest(intent);
      const result = await api(request.path, { method: "POST", headers: { "X-PU-Expected-User": String(userId) }, body: JSON.stringify(request.body) });
      requireOfflineSession(session); if (live.current !== captured) return;
      requireStockAcknowledgement(intent, result);
      await setMeta(stockIntentKey(userId, projectId), null);
      requireOfflineSession(session); if (live.current !== captured) return;
      setPending(null); setQty(""); setSource(""); setMessage(`${labels[intent.kind]} подтверждено.`); setTick(value => value + 1);
    } catch (reason) { if (live.current === captured) setError(`${(reason as Error).message} ${persisted ? "Исходное движение сохранено для безопасного повтора." : "Движение не отправлено: локальный повтор не сохранён. Не закрывайте страницу."}`); }
    finally { if (live.current === captured) { busyRef.current = false; setBusy(false); } }
  }
  function create() {
    if (!ready || pending || busyRef.current || !history || history.balance === null || !item?.unit?.trim() || (history?.unit != null && history.unit !== item.unit.trim())) return;
    try {
      if (!crypto.randomUUID) throw new Error("Для безопасного повтора нужен защищённый браузер");
      const intent = checkedStockIntent({ version: 1, userId, projectId, itemId, kind, qty: stockQuantity(qty), unit: item.unit.trim(), operationKey: crypto.randomUUID(), ...(kind === "writeoff" ? { entryId } : { source: source.trim() || null }) }, userId, projectId);
      void send(intent);
    } catch (reason) { setError((reason as Error).message); }
  }
  function storno(row: Movement) {
    if (!ready || pending || busyRef.current || row.kind !== "writeoff" || row.reversed) return;
    if (!window.confirm(`Сторнировать списание ${row.qty} ${row.unit}? Исходная запись сохранится в истории.`)) return;
    try {
      const intent = checkedStockIntent({ version: 1, userId, projectId, itemId, kind: "storno", qty: stockQuantity(row.qty), unit: row.unit, operationKey: crypto.randomUUID(), reversesId: row.id }, userId, projectId);
      void send(intent);
    } catch (reason) { setError((reason as Error).message); }
  }
  async function discard() {
    if (!pending || busy || !window.confirm("Удалить только локальный повтор? Это не отменяет уже записанное на сервере движение. Перед новым вводом проверьте историю.")) return;
    const captured = scope;
    try { await setMeta(stockIntentKey(userId, projectId), null); if (live.current === captured) { setPending(null); setError(""); } }
    catch (reason) { if (live.current === captured) setError((reason as Error).message); }
  }
  return <section aria-label="Физический склад">
    <h3>Физические движения материалов</h3>
    <p>Оплата и заказ не являются поступлением на склад. Списание выполняется отдельным действием по записи журнала.</p>
    <label>Материал<select value={itemId || ""} disabled={!!pending || busy} onChange={event => { setItemId(Number(event.target.value)); setError(""); setMessage(""); }}>
      <option value="">Выберите материал</option>{items.map(row => <option key={row.id} value={row.id}>{row.name}{row.unit ? `, ${row.unit}` : ""}</option>)}
    </select></label>
    {history && <p>Физический остаток: <strong>{history.balance ?? "не определён"} {history.unit || ""}</strong></p>}
    {error && <p role="alert">{error}</p>}{!ready && userId > 0 && <button type="button" onClick={() => setRestoreTick(value => value + 1)}>Повторить чтение локальных данных</button>}{message && <p role="status">{message}</p>}
    {pending && <div role="status"><p>Ожидает подтверждения: {labels[pending.kind]}, {pending.qty} {pending.unit}. Новое движение заблокировано.</p>
      <button type="button" disabled={busy || !canManage} onClick={() => void send(pending)}>Повторить исходное движение</button>
      <button type="button" disabled={busy} onClick={() => void discard()}>Убрать локальный повтор</button></div>}
    {history?.warning && <p role="alert">{history.warning}</p>}
    {history && item?.unit && history.unit && history.unit !== item.unit.trim() && <p role="alert">Единица материала отличается от складской истории. Новые движения заблокированы до сверки.</p>}
    {canManage && !pending && <form onSubmit={event => { event.preventDefault(); create(); }}>
      <label>Движение<select value={kind} onChange={event => setKind(event.target.value as "receipt" | "writeoff")}><option value="receipt">Поступление</option><option value="writeoff">Списание по записи журнала</option></select></label>
      <label>Количество<input inputMode="decimal" maxLength={64} value={qty} onChange={event => setQty(event.target.value)} /></label>
      <span>{item?.unit || "Единица материала не задана; движение не отправляется"}</span>
      {kind === "receipt" ? <label>Документ-основание<input maxLength={500} value={source} onChange={event => setSource(event.target.value)} /></label>
        : <label>Запись журнала<select value={entryId || ""} onChange={event => setEntryId(Number(event.target.value))}><option value="">Выберите запись</option>{entries.map(row => <option key={row.id} value={row.id}>{row.work_date}: {row.work_name}</option>)}</select></label>}
      <button type="submit" disabled={!ready || busy || !history || history.balance === null || !item?.unit || (history.unit != null && history.unit !== item.unit.trim()) || (kind === "writeoff" && !entryId)}>Учесть движение</button>
    </form>}
    {history && <><h4>Последние {history.limit} движений</h4><ul>{history.movements.map(row => <li key={`${row.kind}:${row.id}`}>
      <time dateTime={row.created_at}>{row.created_at}</time>{" · "}{labels[row.kind]}: {row.qty} {row.unit}{row.work_log_entry_id ? ` · запись журнала № ${row.work_log_entry_id}` : ""}{row.source_document_ref ? ` · ${row.source_document_ref}` : ""}{row.reversed ? " · сторнировано" : ""}
      {canManage && row.kind === "writeoff" && !row.reversed && <button type="button" disabled={!ready || busy || !!pending} onClick={() => storno(row)}>Сторнировать списание</button>}
    </li>)}</ul>{history.has_more && <p>Показана только последняя часть истории.</p>}</>}
  </section>;
}
