import { useEffect, useState } from "react";
import { api, csrfHeaders } from "../../api/client";

type System = { name: string; mode: string; result: "pass" | "fail" };
const COMMON: [string, string][] = [
  ["act_no", "№ акта"], ["date", "Дата"], ["place", "Место составления"], ["object_name", "Объект"], ["address", "Адрес"],
  ["customer", "Заказчик"], ["contractor", "Подрядчик"],
  ["customer_rep", "Представитель заказчика"], ["contractor_rep", "Представитель подрядчика"], ["operator_rep", "Представитель эксплуатации"],
];
const TRIAL_EXTRA: [string, string][] = [["program_ref", "Программа опробования"], ["start", "Начало (дата, время)"], ["end", "Окончание (дата, время)"], ["hours", "Часов"]];
const COMMISSION_EXTRA: [string, string][] = [["trial_act_no", "№ акта опробования"], ["trial_act_date", "Дата акта опробования"], ["scope", "Что вводится (оборудование, системы)"], ["warranty", "Гарантийный срок"]];

async function download(url: string, body: unknown, filename: string) {
  const response = await fetch(url, {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...csrfHeaders() }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `Не удалось сформировать документ (${response.status})`;
    try { const b = await response.json(); if (b?.detail) detail = String(b.detail); } catch { /* keep default */ }
    throw new Error(detail);
  }
  const href = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(href);
}

export function HandoverDocs({ projectId, objectName }: { projectId: number; objectName?: string }) {
  const [header, setHeader] = useState<Record<string, string>>({ object_name: objectName ?? "", date: new Date().toISOString().slice(0, 10), hours: "72", warranty: "24 месяца" });
  const [systems, setSystems] = useState<System[]>([{ name: "", mode: "", result: "pass" }]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const base = `/projects/${projectId}/executive-docs`;

  useEffect(() => {
    const q = objectName ? `?object_name=${encodeURIComponent(objectName)}` : "";
    api<Record<string, string>>(`${base}/handover/defaults${q}`)
      .then((d) => setHeader((h) => ({ ...h, ...d })))
      .catch(() => { /* fields stay editable */ });
  }, [base, objectName]);

  const run = async (url: string, filename: string, items?: unknown) => {
    setBusy(true);
    setError("");
    try {
      await download(`${base}/${url}`, { header, items: items ?? [], object_name: header.object_name || objectName || null }, filename);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const setSys = (i: number, patch: Partial<System>) => setSystems((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const fields = (extra: [string, string][]) => (
    <div className="id-form">
      {[...COMMON, ...extra].map(([key, label]) => (
        <label key={key}>{label}
          <input type={key === "date" ? "date" : "text"} value={header[key] ?? ""} onChange={(e) => setHeader((h) => ({ ...h, [key]: e.target.value }))} />
        </label>
      ))}
    </div>
  );

  return (
    <section className="id-object">
      <h3>Сдача объекта</h3>
      <p className="id-hint">Реквизиты, заказчик, подрядчик и представители подставляются из проекта и вкладки «Участники».</p>
      <p>
        <button type="button" disabled={busy} onClick={() => void run("equipment-register.docx", "Ведомость смонтированного оборудования.docx")}>Ведомость оборудования (Word)</button>{" "}
        <button type="button" disabled={busy} onClick={() => void run("equipment-register.xlsx", "Ведомость смонтированного оборудования.xlsx")}>Ведомость оборудования (Excel)</button>{" "}
        <button type="button" disabled={busy} onClick={() => void run("id-register.docx", "Реестр ИД.docx")}>Реестр ИД (Word)</button>{" "}
        <button type="button" disabled={busy} onClick={() => void run("id-register.xlsx", "Реестр ИД.xlsx")}>Реестр ИД (Excel)</button>
      </p>
      <details>
        <summary>Акт комплексного опробования (72 часа, опытное испытание)</summary>
        {fields(TRIAL_EXTRA)}
        <table>
          <thead><tr><th>Система / установка</th><th>Режим и нагрузка</th><th>Итог</th><th></th></tr></thead>
          <tbody>
            {systems.map((s, i) => (
              <tr key={i}>
                <td><input aria-label={`Система ${i + 1}`} value={s.name} onChange={(e) => setSys(i, { name: e.target.value })} /></td>
                <td><input aria-label={`Режим ${i + 1}`} value={s.mode} onChange={(e) => setSys(i, { mode: e.target.value })} placeholder="нагрузка 60 %" /></td>
                <td>
                  <select aria-label={`Итог системы ${i + 1}`} value={s.result} onChange={(e) => setSys(i, { result: e.target.value as "pass" | "fail" })}>
                    <option value="pass">Без замечаний</option><option value="fail">Есть замечания</option>
                  </select>
                </td>
                <td><button type="button" aria-label={`Убрать систему ${i + 1}`} onClick={() => setSystems((x) => (x.length > 1 ? x.filter((_, j) => j !== i) : [{ name: "", mode: "", result: "pass" }]))}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          <button type="button" onClick={() => setSystems((x) => [...x, { name: "", mode: "", result: "pass" }])}>Добавить систему</button>{" "}
          <button type="button" disabled={busy} onClick={() => void run("trial-act.docx", "Акт комплексного опробования.docx", systems)}>Скачать акт</button>
        </p>
      </details>
      <details>
        <summary>Акт ввода в эксплуатацию</summary>
        {fields(COMMISSION_EXTRA)}
        <p><button type="button" disabled={busy} onClick={() => void run("commissioning-act.docx", "Акт ввода в эксплуатацию.docx")}>Скачать акт</button></p>
      </details>
      {error && <p role="alert" className="id-missing">{error}</p>}
    </section>
  );
}
