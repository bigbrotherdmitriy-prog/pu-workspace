import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { CostCategory } from "./types";

type Props = { projectId: number; contractId: number; documentId: number; planYear: number;
  categories: CostCategory[]; onApplied: () => void };

type Preview = {
  document_version_id: number; document_sha256: string; preview_hash: string;
  expense_total: string; currency: string; warnings: string[];
  existing_expense_total?: string; existing_expense_difference?: string | null;
  conflicts: { code: string; message: string }[];
  articles: { article_id: number; title: string; source_coordinate: string; monthly_total: string;
    annual_total: string | null; annual_difference: string | null; budget_line_id: number | null;
    months: { month: number; source_coordinate: string; raw_amount: string; ordinary_amount: string;
      amount: string; adjustment: string }[] }[];
  existing_rows: { id: number; title: string; source_coordinate: string; amount: string;
    planned_date: string; record_version: number; status: string; current_budget_line_id: number | null;
    proposed_budget_line_id: number | null; proposed_article_id: number | null; source_difference: string | null }[];
};
type Operation = { operation_id: number; created_budget_ids: number[]; used_budget_ids: number[];
  created_cash_flow_ids: number[]; status: string; undone?: boolean };

export function ArticleBudgetPreviewPanel({ projectId, contractId, documentId, planYear, categories, onApplied }: Props) {
  const [mode, setMode] = useState("create_budget");
  const [revision, setRevision] = useState(1);
  const [year, setYear] = useState(planYear);
  const [choices, setChoices] = useState<Record<number, number>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewConfig, setPreviewConfig] = useState("");
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const sequence = useRef(0);
  const key = `${projectId}:${contractId}:${documentId}:${planYear}`;
  const context = useRef({ key });
  if (context.current.key !== key) context.current = { key };
  const attempt = useRef<{ signature: string; id: string } | null>(null);
  const body = { project_id: projectId, contract_id: contractId, plan_year: year,
    budget_revision: revision, mode, category_by_article: choices };
  const config = JSON.stringify(body);

  async function refresh(requestBody = body) {
    const captured = context.current;
    const requestSequence = ++sequence.current;
    setBusy(true); setError("");
    try {
      const result = await api<Preview>(`/execution/documents/${documentId}/article-budget-preview`, {
        method: "POST", body: JSON.stringify(requestBody),
      });
      if (context.current !== captured || requestSequence !== sequence.current) return;
      setPreview(result); setPreviewConfig(JSON.stringify(requestBody)); setOperation(null);
    } catch (caught) {
      if (context.current === captured && requestSequence === sequence.current) {
        setError((caught as Error).message); setPreviewConfig("");
      }
    } finally {
      if (context.current === captured && requestSequence === sequence.current) setBusy(false);
    }
  }

  useEffect(() => {
    setMode("create_budget"); setRevision(1); setYear(planYear); setChoices({}); setPreview(null); setOperation(null);
    setPreviewConfig(""); attempt.current = null; pending.current = false;
    if (projectId && contractId) void refresh({ project_id: projectId, contract_id: contractId,
      plan_year: planYear, budget_revision: 1, mode: "create_budget", category_by_article: {} });
    return () => { ++sequence.current; };
    // Context reset, not a request on every category edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const ready = Boolean(preview && config === previewConfig && !preview.conflicts.length && !operation && !busy);
  async function apply() {
    if (!ready || !preview || pending.current) return;
    if (!window.confirm(mode === "create_budget"
      ? `Создать ${preview.articles.length} предложений бюджета? Старые ДДС не изменятся. Утверждение бюджета — отдельно.`
      : `Создать предложения бюджета и нового расходного прогноза? Это не счета, не обязательства и не платежи. Старые ДДС не изменятся.`)) return;
    const captured = context.current;
    const payload = { ...body, preview_hash: preview.preview_hash,
      expected_document_version_id: preview.document_version_id,
      expected_document_sha256: preview.document_sha256, owner_confirmed: true };
    const signature = JSON.stringify(payload);
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: crypto.randomUUID() };
    pending.current = true; setBusy(true); setError("");
    try {
      const result = await api<Operation>(`/execution/documents/${documentId}/article-budget-import`, {
        method: "POST", body: JSON.stringify({ ...payload, idempotency_key: attempt.current.id }),
      });
      if (context.current !== captured) return;
      setOperation(result); onApplied();
    } catch (caught) {
      if (context.current === captured) setError((caught as Error).message);
    } finally {
      if (context.current === captured) { pending.current = false; setBusy(false); }
    }
  }

  async function undo() {
    if (!operation || busy || !window.confirm("Отменить только эту операцию? При последующих изменениях отмена будет заблокирована.")) return;
    const captured = context.current; setBusy(true); setError("");
    try {
      const result = await api<Operation>(`/execution/article-budget-operations/${operation.operation_id}/undo`, { method: "POST" });
      if (context.current === captured) { setOperation(result); onApplied(); }
    } catch (caught) {
      if (context.current === captured) setError((caught as Error).message);
    } finally { if (context.current === captured) setBusy(false); }
  }

  return <section aria-label="Бюджет из статей ДДС" className="card structured-import">
    <h3>Создать расходный бюджет из статей</h3>
    <p>Период: {year}. Источник суммы — месячные ячейки, годовая колонка — контроль. Существующие записи ДДС не изменяются. Категорию каждой статьи выберите явно.</p>
    <p>Подтверждение прогноза без этапа ГПР — следующий отдельный шаг. Созданные здесь предложения не входят в утверждённые итоги и не являются платежами.</p>
    <div className="structured-actions">
      <label>Режим<select aria-label="Режим бюджетного импорта" value={mode} disabled={busy || Boolean(operation)} onChange={(event) => setMode(event.target.value)}>
        <option value="create_budget">Только предложения бюджета</option><option value="import_forecast">Бюджет и новый расходный прогноз (без приходов)</option>
      </select></label>
      <label>Ревизия<input aria-label="Ревизия расходного бюджета" type="number" min={1} max={10000} value={revision} disabled={busy || Boolean(operation)} onChange={(event) => setRevision(Number(event.target.value))} /></label>
      <label>Год бюджета<input aria-label="Год расходного бюджета" type="number" min={2000} max={2100} value={year} disabled={busy || Boolean(operation)} onChange={(event) => setYear(Number(event.target.value))} /></label>
      <button type="button" disabled={busy || !contractId} onClick={() => void refresh()}>Обновить предпросмотр бюджета</button>
    </div>
    {error && <p role="alert" className="finance-warning">{error}</p>}
    {preview?.warnings.map((warning, index) => <p className="finance-warning" key={index}>{warning}</p>)}
    {preview?.conflicts.map((conflict, index) => <p role="alert" className="finance-warning" key={index}>{conflict.code}: {conflict.message}</p>)}
    {preview && <><p>Расходных статей: {preview.articles.length}. Всего: {preview.expense_total} {preview.currency}.</p>
      <div className="structured-table"><table><thead><tr><th>Источник / статья</th><th>Сумма месяцев</th><th>Годовой контроль / разница</th><th>Категория затрат</th><th>Проверка округления</th></tr></thead><tbody>
        {preview.articles.map((article) => <tr key={article.article_id}>
          <td>{article.source_coordinate}<br />{article.title}{article.budget_line_id && <small> · Строка бюджета #{article.budget_line_id} (без прибавления суммы)</small>}</td>
          <td>{article.monthly_total}</td><td>{article.annual_total ?? "—"} / {article.annual_difference ?? "—"}</td>
          <td><select aria-label={`Категория статьи ${article.title}`} value={choices[article.article_id] || ""} disabled={busy || Boolean(operation)} onChange={(event) => setChoices((current) => ({ ...current, [article.article_id]: Number(event.target.value) }))}>
            <option value="">Выберите категорию</option>{categories.filter((item) => item.is_active).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></td>
          <td><details><summary>Месяцы и остатки</summary>{article.months.map((cell) => <p key={cell.source_coordinate}>{cell.source_coordinate}: raw {cell.raw_amount}; округление {cell.ordinary_amount}; поправка {cell.adjustment}; итог {cell.amount}</p>)}</details></td>
        </tr>)}
      </tbody></table></div>
      <details><summary>Предпросмотр существующих записей ({preview.existing_rows.length}), без применения</summary>
        {preview.existing_expense_difference != null && <p>Текущие расходы: {preview.existing_expense_total}; отличие от бюджета: {preview.existing_expense_difference}. Суммы не подгоняются.</p>}
        <div className="structured-table"><table><thead><tr><th>ID / источник</th><th>Статья / сумма / дата</th><th>Статус / версия</th><th>Связи до → предложение</th></tr></thead><tbody>
          {preview.existing_rows.map((row) => <tr key={row.id}><td>#{row.id} · {row.source_coordinate}</td><td>{row.title}<br />{row.amount} · {row.planned_date}{row.source_difference !== null && row.source_difference !== "0.00" && <p>Ручное отличие: {row.source_difference}</p>}</td>
            <td>{row.status} · v{row.record_version}</td><td>{row.current_budget_line_id ?? "нет"} → {row.proposed_budget_line_id ? `#${row.proposed_budget_line_id}` : row.proposed_article_id ? `статья ${row.proposed_article_id}` : "не определено"}. Применение недоступно.</td></tr>)}
        </tbody></table></div>
      </details>
    </>}
    <div className="structured-actions"><button type="button" disabled={!ready} onClick={() => void apply()}>Подтвердить создание предложений {mode === "create_budget" ? "бюджета" : "бюджета и прогноза"}</button>
      {operation && <><span>Операция #{operation.operation_id}: {operation.undone ? "отменена, история сохранена" : `proposed; бюджет: ${operation.created_budget_ids.length}, новый ДДС: ${operation.created_cash_flow_ids.length}`}</span>
        {!operation.undone && <button type="button" disabled={busy} className="secondary" onClick={() => void undo()}>Отменить эту операцию</button>}</>}
    </div>
  </section>;
}
