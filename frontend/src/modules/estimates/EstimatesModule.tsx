import { useCallback, useEffect, useMemo, useState } from "react";
import { api, csrfHeaders } from "../../api/client";
import "./estimates.css";

type EstimateRow = { id: number; name: string; source_name: string | null; total: string | null; vat_rate: string; lines_count: number };
type Line = {
  id: number; section: string | null; position: string | null; code: string | null; name: string; unit: string | null;
  qty: string | null; unit_price: string | null; total: string | null; fact_qty: string; billed_qty: string; done_percent: number | null;
};
type Detail = EstimateRow & { lines: Line[]; done_amount: string };
type Preview = { lines: { position: string; name: string; unit: string; qty: string | null; unit_price: string | null; total: string | null; section: string }[]; lines_total: number; warnings: string[]; detected: { columns: Record<string, number> } };
type Act = { id: number; number: string; period_from: string; period_to: string; status: string; total_ex_vat: string; lines_count: number };
type ActPreview = { lines: { position: string; name: string; unit: string; qty: string; unit_price: string; amount: string }[]; total_ex_vat: string; warnings: string[] };

const money = (v: string | null | undefined) => (v == null ? "—" : Number(v).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const num = (v: string | null | undefined) => (v == null ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 3 }));
const dateRu = (v: string) => v.split("-").reverse().join(".");
const today = () => new Date().toISOString().slice(0, 10);
const monthStart = () => `${today().slice(0, 8)}01`;

const HEADER_FIELDS: [string, string][] = [
  ["investor", "Инвестор"], ["customer", "Заказчик (генподрядчик)"], ["contractor", "Подрядчик"], ["site", "Стройка"],
  ["object_name", "Объект"], ["contract_no", "Договор №"], ["contract_date", "Дата договора"],
  ["customer_rep", "Принял: должность"], ["customer_rep_name", "Принял: ФИО"], ["contractor_rep", "Сдал: должность"], ["contractor_rep_name", "Сдал: ФИО"],
];
const headerKey = (projectId: number) => `pu-ks-header:${projectId}`;

async function saveBlob(response: Response, filename: string) {
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function EstimatesModule({ projectId }: { projectId: number }) {
  const [estimates, setEstimates] = useState<EstimateRow[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [acts, setActs] = useState<Act[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [vat, setVat] = useState("22");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [period, setPeriod] = useState({ from: monthStart(), to: today() });
  const [actPreview, setActPreview] = useState<ActPreview | null>(null);
  const [header, setHeader] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem(headerKey(projectId)) || "{}") as Record<string, string>; } catch { return {}; }
  });

  const fail = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback);

  useEffect(() => {
    if (projectId <= 0) return;
    api<{ estimates: EstimateRow[] }>(`/projects/${projectId}/estimates`)
      .then((res) => {
        const list = res?.estimates ?? [];
        setEstimates(list);
        setSelected((cur) => (cur && list.some((e) => e.id === cur) ? cur : list[0]?.id ?? null));
      })
      .catch((e) => fail(e, "Не удалось загрузить сметы"));
  }, [projectId, tick]);

  useEffect(() => {
    if (!selected) { setDetail(null); setActs([]); return; }
    api<Detail>(`/projects/${projectId}/estimates/${selected}`).then(setDetail).catch((e) => fail(e, "Не удалось открыть смету"));
    api<{ acts: Act[] }>(`/projects/${projectId}/estimates/${selected}/ks-acts`).then((r) => setActs(r?.acts ?? [])).catch(() => undefined);
  }, [projectId, selected, tick]);

  const reload = useCallback(() => setTick((v) => v + 1), []);

  async function sendFile(previewOnly: boolean) {
    if (!file) { setError("Выберите файл сметы (.xlsx)"); return; }
    if (!previewOnly && !name.trim()) { setError("Укажите название сметы"); return; }
    setError("");
    setMessage("");
    const query = new URLSearchParams({ name: name.trim() || file.name, vat_rate: vat, source_name: file.name });
    if (previewOnly) query.set("preview", "true");
    try {
      const response = await fetch(`/projects/${projectId}/estimates/import?${query.toString()}`, {
        method: "POST", body: file, credentials: "same-origin", headers: csrfHeaders(),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Не удалось разобрать файл (${response.status})`);
      if (previewOnly) { setPreview(body as Preview); return; }
      setPreview(null);
      setFile(null);
      setName("");
      setSelected(body.id);
      setMessage(`Смета загружена: позиций ${body.lines_count}.${body.warnings?.length ? ` Предупреждений: ${body.warnings.length}.` : ""}`);
      reload();
    } catch (e) {
      fail(e, "Не удалось загрузить смету");
    }
  }

  async function patchLine(line: Line, field: "qty" | "unit_price", value: string) {
    if ((line[field] ?? "") === value.trim().replace(",", ".")) return;
    try {
      await api(`/projects/${projectId}/estimate-lines/${line.id}`, { method: "PATCH", body: JSON.stringify({ [field]: value }) });
      reload();
    } catch (e) {
      fail(e, "Не удалось сохранить");
      reload();
    }
  }

  async function removeEstimate() {
    if (!selected) return;
    try {
      await api(`/projects/${projectId}/estimates/${selected}`, { method: "DELETE" });
      setSelected(null);
      reload();
    } catch (e) {
      fail(e, "Не удалось удалить смету");
    }
  }

  async function previewAct() {
    if (!selected) return;
    setError("");
    try {
      const res = await api<ActPreview>(`/projects/${projectId}/estimates/${selected}/ks-preview?period_from=${period.from}&period_to=${period.to}`);
      setActPreview(res);
    } catch (e) {
      setActPreview(null);
      fail(e, "Не удалось посчитать акт");
    }
  }

  async function createAct() {
    if (!selected) return;
    setError("");
    try {
      await api(`/projects/${projectId}/estimates/${selected}/ks-acts`, { method: "POST", body: JSON.stringify({ period_from: period.from, period_to: period.to }) });
      setActPreview(null);
      setMessage("Акт сформирован из факта журнала работ.");
      reload();
    } catch (e) {
      fail(e, "Не удалось создать акт");
    }
  }

  async function actStatus(act: Act, status: string) {
    try { await api(`/projects/${projectId}/ks-acts/${act.id}`, { method: "PATCH", body: JSON.stringify({ status }) }); reload(); } catch (e) { fail(e, "Не удалось изменить статус"); }
  }

  async function actDelete(act: Act) {
    try { await api(`/projects/${projectId}/ks-acts/${act.id}`, { method: "DELETE" }); reload(); } catch (e) { fail(e, "Не удалось удалить акт"); }
  }

  async function download(act: Act, kind: "ks2.docx" | "ks3.docx" | "ks.xlsx", filename: string) {
    setError("");
    try { localStorage.setItem(headerKey(projectId), JSON.stringify(header)); } catch { /* optional */ }
    try {
      const response = await fetch(`/projects/${projectId}/ks-acts/${act.id}/${kind}`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ header }),
      });
      if (!response.ok) throw new Error(`Не удалось сформировать документ (${response.status})`);
      await saveBlob(response, filename);
    } catch (e) {
      fail(e, "Не удалось скачать");
    }
  }

  const sections = useMemo(() => {
    const out: { title: string; lines: Line[] }[] = [];
    for (const l of detail?.lines ?? []) {
      const title = l.section || "";
      const last = out[out.length - 1];
      if (last && last.title === title) last.lines.push(l);
      else out.push({ title, lines: [l] });
    }
    return out;
  }, [detail]);

  return (
    <div className="est">
      <div className="est-head">
        <h3 className="est-title">Сметы и КС</h3>
        {estimates.length > 0 && (
          <select aria-label="Смета" value={selected ?? ""} onChange={(e) => setSelected(Number(e.target.value))}>
            {estimates.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        )}
      </div>
      {message && <p className="est-note" role="status">{message}</p>}
      {error && <p className="est-error" role="alert">{error}</p>}

      <details className="est-box" open={estimates.length === 0}>
        <summary>Загрузить смету из Excel</summary>
        <p className="est-note">Подойдёт выгрузка из Гранд-Сметы, Смета.Ру или обычная таблица Excel (.xlsx) с колонками «Наименование», «Ед. изм.», «Количество», «Цена», «Всего».</p>
        <div className="est-form">
          <label>Файл сметы<input type="file" accept=".xlsx" aria-label="Файл сметы" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); if (!name && e.target.files?.[0]) setName(e.target.files[0].name.replace(/\.xlsx$/i, "")); }} /></label>
          <label>Название сметы<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>НДС, %<input inputMode="decimal" value={vat} onChange={(e) => setVat(e.target.value)} /></label>
          <button type="button" onClick={() => void sendFile(true)}>Проверить файл</button>
        </div>
        {preview && (
          <div>
            <p className="est-note">Найдено позиций: {preview.lines_total}. {preview.warnings.length ? `Предупреждения: ${preview.warnings.slice(0, 3).join("; ")}` : "Замечаний нет."}</p>
            <div className="est-wrap"><table className="est-table"><thead><tr><th>№</th><th>Наименование</th><th>Ед.</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead>
              <tbody>{preview.lines.slice(0, 8).map((l, i) => <tr key={i}><td>{l.position}</td><td>{l.name}</td><td>{l.unit}</td><td>{num(l.qty)}</td><td>{money(l.unit_price)}</td><td>{money(l.total)}</td></tr>)}</tbody></table></div>
            <button type="button" className="est-main" onClick={() => void sendFile(false)}>Сохранить смету</button>
          </div>
        )}
      </details>

      {detail && (
        <>
          <p className="est-note">Позиций: {detail.lines_count}. Стоимость по смете: <b>{money(detail.total)}</b> ₽. Выполнено по журналу: <b>{money(detail.done_amount)}</b> ₽. НДС {num(detail.vat_rate)}%.
            {" "}<button type="button" onClick={() => void removeEstimate()}>Удалить смету</button></p>
          <div className="est-wrap">
            <table className="est-table">
              <thead><tr><th>№</th><th>Шифр</th><th>Наименование</th><th>Ед.</th><th>Объём</th><th>Цена</th><th>Стоимость</th><th>Факт (журнал)</th><th>%</th><th>В актах</th></tr></thead>
              <tbody>
                {sections.map((s, si) => (
                  <>
                    {s.title && <tr key={`s${si}`} className="est-section"><td colSpan={10}>{s.title}</td></tr>}
                    {s.lines.map((l) => (
                      <tr key={l.id}>
                        <td>{l.position}</td><td>{l.code}</td><td>{l.name}</td><td>{l.unit}</td>
                        <td><input aria-label={`Объём позиции ${l.position || l.id}`} size={7} defaultValue={l.qty ?? ""} key={`q${l.id}-${l.qty}`} onBlur={(e) => void patchLine(l, "qty", e.target.value)} /></td>
                        <td><input aria-label={`Цена позиции ${l.position || l.id}`} size={8} defaultValue={l.unit_price ?? ""} key={`p${l.id}-${l.unit_price}`} onBlur={(e) => void patchLine(l, "unit_price", e.target.value)} /></td>
                        <td>{money(l.total)}</td><td>{num(l.fact_qty)}</td><td>{l.done_percent == null ? "—" : `${l.done_percent}`}</td><td>{num(l.billed_qty)}</td>
                      </tr>
                    ))}
                  </>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="est-sub">Акты КС-2 и КС-3</h3>
          <p className="est-note">Объёмы берутся только из журнала работ (запись с выбранной позицией сметы), цены из сметы. Уже выставленное в прошлых актах повторно не включается.</p>
          <div className="est-form">
            <label>Период с<input type="date" value={period.from} onChange={(e) => setPeriod((p) => ({ ...p, from: e.target.value }))} /></label>
            <label>по<input type="date" value={period.to} onChange={(e) => setPeriod((p) => ({ ...p, to: e.target.value }))} /></label>
            <button type="button" onClick={() => void previewAct()}>Что войдёт в акт</button>
          </div>
          {actPreview && (
            <div>
              {actPreview.warnings.length > 0 && <ul className="est-error">{actPreview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
              {actPreview.lines.length === 0 ? <p className="est-empty">За период нет выполненных работ по позициям сметы.</p> : (
                <>
                  <div className="est-wrap"><table className="est-table"><thead><tr><th>№</th><th>Наименование</th><th>Ед.</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead>
                    <tbody>{actPreview.lines.map((l, i) => <tr key={i}><td>{l.position}</td><td>{l.name}</td><td>{l.unit}</td><td>{num(l.qty)}</td><td>{money(l.unit_price)}</td><td>{money(l.amount)}</td></tr>)}</tbody></table></div>
                  <p>Итого без НДС: <b>{money(actPreview.total_ex_vat)}</b> ₽. <button type="button" className="est-main" onClick={() => void createAct()}>Сформировать акт</button></p>
                </>
              )}
            </div>
          )}

          <details className="est-box">
            <summary>Реквизиты для печатных форм</summary>
            <div className="est-form">
              {HEADER_FIELDS.map(([k, label]) => (
                <label key={k}>{label}<input type={k === "contract_date" ? "date" : "text"} value={header[k] ?? ""} onChange={(e) => setHeader((h) => ({ ...h, [k]: e.target.value }))} /></label>
              ))}
            </div>
          </details>

          {acts.length === 0 ? <p className="est-empty">Актов пока нет.</p> : (
            <ul className="est-acts">
              {acts.map((a) => (
                <li key={a.id}>
                  <b>Акт № {a.number}</b> · {dateRu(a.period_from)} – {dateRu(a.period_to)} · {money(a.total_ex_vat)} ₽ без НДС · {a.status === "signed" ? "подписан" : "черновик"}
                  <div className="est-actions">
                    <button type="button" onClick={() => void download(a, "ks2.docx", `КС-2 № ${a.number}.docx`)}>КС-2 (Word)</button>
                    <button type="button" onClick={() => void download(a, "ks3.docx", `КС-3 № ${a.number}.docx`)}>КС-3 (Word)</button>
                    <button type="button" onClick={() => void download(a, "ks.xlsx", `КС № ${a.number}.xlsx`)}>Excel</button>
                    {a.status !== "signed" && <button type="button" onClick={() => void actStatus(a, "signed")}>Отметить подписанным</button>}
                    {a.status !== "signed" && <button type="button" aria-label={`Удалить акт ${a.number}`} onClick={() => void actDelete(a)}>Удалить</button>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {!detail && estimates.length === 0 && <p className="est-empty">Смет пока нет. Загрузите файл Excel выше.</p>}
    </div>
  );
}
