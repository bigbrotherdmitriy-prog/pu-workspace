import { useState } from "react";

type Item = { name: string; type_mark: string; serial: string; location: string; program: string; values: string; result: "pass" | "fail" };
const emptyItem = (): Item => ({ name: "", type_mark: "", serial: "", location: "", program: "", values: "", result: "pass" });

const HEADER_FIELDS: [string, string][] = [
  ["act_no", "№ акта"], ["date", "Дата"], ["place", "Место составления"], ["object_name", "Объект"], ["address", "Адрес"],
  ["customer", "Заказчик"], ["contractor", "Подрядчик (монтажная организация)"], ["program_ref", "Программа / методика испытаний"],
  ["customer_rep", "Представитель заказчика"], ["contractor_rep", "Представитель подрядчика"], ["operator_rep", "Представитель эксплуатации"],
];

export function EquipmentTestAct({ projectId, objectName }: { projectId: number; objectName?: string }) {
  const [header, setHeader] = useState<Record<string, string>>({ object_name: objectName ?? "", date: new Date().toISOString().slice(0, 10) });
  const [items, setItems] = useState<Item[]>([emptyItem()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setItem = (i: number, patch: Partial<Item>) => setItems((r) => r.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/projects/${projectId}/executive-docs/equipment-test-act.docx`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ header, items }),
      });
      if (!response.ok) {
        let detail = `Не удалось сформировать акт (${response.status})`;
        try { const body = await response.json(); if (body?.detail) detail = String(body.detail); } catch { /* keep default */ }
        throw new Error(detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Акт испытания оборудования.docx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="id-equipment-act">
      <summary>Акт индивидуального испытания оборудования (Word)</summary>
      <p>Впишите оборудование и результаты испытаний. Заключение акта составится по результатам.</p>
      <div className="id-form">
        {HEADER_FIELDS.map(([key, label]) => (
          <label key={key}>{label}
            <input type={key === "date" ? "date" : "text"} value={header[key] ?? ""} onChange={(e) => setHeader((h) => ({ ...h, [key]: e.target.value }))} />
          </label>
        ))}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>Оборудование</th><th>Тип, марка</th><th>Заводской №</th><th>Место</th><th>Что проведено</th><th>Результаты замеров</th><th>Итог</th><th></th></tr></thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i}>
                <td><input aria-label={`Оборудование ${i + 1}`} value={it.name} onChange={(e) => setItem(i, { name: e.target.value })} /></td>
                <td><input aria-label={`Тип оборудования ${i + 1}`} value={it.type_mark} onChange={(e) => setItem(i, { type_mark: e.target.value })} /></td>
                <td><input aria-label={`Заводской номер ${i + 1}`} value={it.serial} onChange={(e) => setItem(i, { serial: e.target.value })} /></td>
                <td><input aria-label={`Место установки ${i + 1}`} value={it.location} onChange={(e) => setItem(i, { location: e.target.value })} /></td>
                <td><input aria-label={`Проведено ${i + 1}`} value={it.program} onChange={(e) => setItem(i, { program: e.target.value })} placeholder="Пробный пуск 2 ч" /></td>
                <td><input aria-label={`Замеры ${i + 1}`} value={it.values} onChange={(e) => setItem(i, { values: e.target.value })} placeholder="ток 4,1 А" /></td>
                <td>
                  <select aria-label={`Итог ${i + 1}`} value={it.result} onChange={(e) => setItem(i, { result: e.target.value as "pass" | "fail" })}>
                    <option value="pass">Выдержало</option><option value="fail">Не выдержало</option>
                  </select>
                </td>
                <td><button type="button" aria-label={`Убрать оборудование ${i + 1}`} onClick={() => setItems((x) => (x.length > 1 ? x.filter((_, j) => j !== i) : [emptyItem()]))}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <button type="button" onClick={() => setItems((r) => [...r, emptyItem()])}>Добавить оборудование</button>{" "}
        <button type="button" disabled={busy} onClick={() => void download()}>{busy ? "Формируем…" : "Скачать акт"}</button>
      </p>
      {error && <p role="alert" className="id-missing">{error}</p>}
    </details>
  );
}
