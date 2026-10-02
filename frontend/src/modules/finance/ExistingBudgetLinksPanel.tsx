import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";

type Props = { projectId: number; contractId: number; documentId: number; planYear: number;
  budgetRevision: number; refreshToken?: number; onApplied: () => void };
type Preview = {
  preview_hash: string; document_version_id: number; document_sha256: string;
  expense_total: string; budget_total: string; difference: string; currency: string; apply_allowed: boolean;
  conflicts: { code: string; message: string; cash_flow_id?: number; budget_line_id?: number }[];
  rows: { id: number; title: string; amount: string; planned_date: string; status: string; record_version: number;
    current_budget_line_id: number | null; budget_line_id: number | null; budget_title: string | null; category: string | null }[];
};
type Operation = { operation_id: number; link_count: number; expense_total: string; created_at: string;
  undone: boolean; active_link_count: number; replayed: boolean };

export function ExistingBudgetLinksPanel({ projectId, contractId, documentId, planYear, budgetRevision, refreshToken = 0, onApplied }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [invalid, setInvalid] = useState(true);
  const [result, setResult] = useState<Operation | null>(null);
  const pending = useRef(false);
  const sequence = useRef(0);
  const key = `${projectId}:${contractId}:${documentId}:${planYear}:${budgetRevision}`;
  const context = useRef({ key });
  if (context.current.key !== key) context.current = { key };
  const attempt = useRef<{ signature: string; id: string } | null>(null);
  const body = { project_id: projectId, contract_id: contractId, plan_year: planYear, budget_revision: budgetRevision };
  const historyPath = `/execution/documents/${documentId}/budget-link-operations?${new URLSearchParams(
    Object.entries(body).map(([name, value]) => [name, String(value)]))}`;

  async function refresh() {
    if (pending.current || !projectId || !contractId) return;
    const captured = context.current;
    const requestSequence = ++sequence.current;
    setBusy(true); setInvalid(true); setError(""); setResult(null);
    // The two reads are independent: a blocked preview must not hide persisted
    // operations, including the whole-batch undo action after a page reload.
    const results = await Promise.allSettled([
      api<Preview>(`/execution/documents/${documentId}/budget-links-preview`, { method: "POST", body: JSON.stringify(body) }),
      api<Operation[]>(historyPath),
    ]);
    if (context.current !== captured || requestSequence !== sequence.current) return;
    const [proposal, history] = results;
    if (proposal.status === "fulfilled") { setPreview(proposal.value); setInvalid(false); }
    else setPreview(null);
    if (history.status === "fulfilled") setOperations(history.value);
    const errors = results.flatMap((item) => item.status === "rejected" ? [(item.reason as Error).message] : []);
    setError(errors.join("; ")); setBusy(false);
  }

  useEffect(() => {
    setPreview(null); setOperations([]); setResult(null); setInvalid(true);
    pending.current = false; attempt.current = null;
    void refresh();
    return () => { ++sequence.current; };
    // Requests follow scope changes, not every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const lastRefreshToken = useRef(refreshToken);
  useEffect(() => {
    if (lastRefreshToken.current !== refreshToken) {
      lastRefreshToken.current = refreshToken;
      void refresh();
    }
    // Refresh after budget creation/undo; no mutation is triggered here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  const ready = Boolean(preview?.apply_allowed && !invalid && !busy && !result && !preview.conflicts.length);
  async function apply() {
    if (!ready || !preview || pending.current) return;
    if (!window.confirm(`Проставить budget_line_id для ${preview.rows.length} расходов на ${preview.expense_total} ${preview.currency} по показанным связям? Суммы, даты и статусы не изменятся.`)) return;
    const captured = context.current;
    const payload = { ...body, preview_hash: preview.preview_hash, expected_document_version_id: preview.document_version_id,
      expected_document_sha256: preview.document_sha256,
      expected_cash_versions: Object.fromEntries(preview.rows.map((row) => [row.id, row.record_version])), owner_confirmed: true };
    const signature = JSON.stringify(payload);
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: crypto.randomUUID() };
    pending.current = true; setBusy(true); setError("");
    try {
      const operation = await api<Operation>(`/execution/documents/${documentId}/budget-links-apply`, {
        method: "POST", body: JSON.stringify({ ...payload, idempotency_key: attempt.current.id }),
      });
      if (context.current !== captured) return;
      setResult(operation); setInvalid(true);
      setOperations((current) => [operation, ...current.filter((item) => item.operation_id !== operation.operation_id)]);
      onApplied();
    } catch (caught) {
      if (context.current !== captured) return;
      setError((caught as Error).message);
      // Network uncertainty can be retried explicitly with the same key.
      // A server rejection always requires a new preview and confirmation.
      if (!(caught instanceof ApiError) || caught.status !== null) setInvalid(true);
    } finally {
      if (context.current === captured) { pending.current = false; setBusy(false); }
    }
  }

  async function undo(operation: Operation) {
    if (busy || pending.current || operation.undone) return;
    if (!window.confirm(`Отменить все ${operation.link_count} связей операции #${operation.operation_id}? Если изменён хотя бы один расход, ни одна связь не будет отменена.`)) return;
    const captured = context.current;
    pending.current = true; setBusy(true); setError("");
    try {
      const undone = await api<Operation>(`/execution/budget-link-operations/${operation.operation_id}/undo`, { method: "POST" });
      if (context.current !== captured) return;
      setResult(undone); setInvalid(true);
      setOperations((current) => current.map((item) => item.operation_id === undone.operation_id ? undone : item));
      onApplied();
    } catch (caught) { if (context.current === captured) setError((caught as Error).message); }
    finally { if (context.current === captured) { pending.current = false; setBusy(false); } }
  }

  return <section aria-label="Связи существующих расходов с бюджетом">
    <h3>Привязать существующие расходы</h3>
    <p>Меняется только budget_line_id и служебная версия записи. Суммы, даты, статусы, этапы ГПР и типы ДДС сохраняются. Это не подтверждение ДДС и не платёж.</p>
    <button type="button" disabled={busy || !contractId} onClick={() => void refresh()}>Обновить предпросмотр связей</button>
    {error && <p role="alert" className="finance-warning">{error}</p>}
    {preview && <>
      <p>Расходов: {preview.rows.length}; сумма: {preview.expense_total} {preview.currency}. Бюджет: {preview.budget_total}; разница: {preview.difference}. Суммы не подгоняются.</p>
      {preview.conflicts.map((item, index) => <p role="alert" className="finance-warning" key={index}>{item.code}{item.cash_flow_id ? ` · ДДС #${item.cash_flow_id}` : ""}: {item.message}</p>)}
      <div className="structured-table"><table><thead><tr><th>Расход</th><th>Сумма / дата</th><th>Статус / версия</th><th>budget_line_id до → после</th><th>Строка бюджета / категория</th></tr></thead><tbody>
        {preview.rows.map((row) => <tr key={row.id}><td>#{row.id} · {row.title}</td><td>{row.amount} {preview.currency}<br />{row.planned_date}</td>
          <td>{row.status} · v{row.record_version}</td><td>{row.current_budget_line_id ?? "нет"} → {row.budget_line_id ?? "не определено"}</td>
          <td>{row.budget_title ?? "—"}<br />{row.category ?? "—"}</td></tr>)}
      </tbody></table></div>
    </>}
    <button type="button" disabled={!ready} onClick={() => void apply()}>Подтвердить применение связей</button>
    {result && <p role="status">Операция связей #{result.operation_id}: {result.undone ? "отменена целиком" : `применено ${result.link_count} связей`}. Суммы, даты и статусы сохранены.</p>}
    {operations.length > 0 && <details open><summary>История операций связей</summary>{operations.map((operation) => <div className="structured-actions" key={operation.operation_id}>
      <span>#{operation.operation_id} · {operation.created_at} · {operation.link_count} связей · {operation.expense_total} · {operation.undone ? "отменена" : "применена"}</span>
      {!operation.undone && <button type="button" disabled={busy} onClick={() => void undo(operation)}>Отменить всю операцию #{operation.operation_id}</button>}
    </div>)}</details>}
  </section>;
}
