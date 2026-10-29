import { useState } from "react";
import { csrfHeaders } from "../../api/client";

const PAIRS = ["A-B", "B-C", "C-A", "A-N", "B-N", "C-N", "A-PE", "B-PE", "C-PE", "N-PE"] as const;
type Row = { line_name: string; cable_mark: string; length_m: string; voltage_v: string; pairs: Record<string, string> };
const emptyRow = (): Row => ({ line_name: "", cable_mark: "", length_m: "", voltage_v: "1000", pairs: {} });

const HEADER_FIELDS: [string, string][] = [
  ["object_name", "Объект"], ["address", "Адрес"], ["customer", "Заказчик"], ["contractor", "Подрядчик"],
  ["laboratory", "Лаборатория / исполнитель"], ["protocol_no", "№ протокола"], ["date", "Дата измерений"],
  ["instrument", "Мегаомметр (тип, №)"], ["instrument_check_date", "Поверка до"], ["temperature", "Температура, °C"],
  ["humidity", "Влажность, %"], ["tested_by", "Измерения выполнил"], ["checked_by", "Проверил"],
];

export function InsulationProtocol({ projectId, objectName }: { projectId: number; objectName?: string }) {
  const [header, setHeader] = useState<Record<string, string>>({ object_name: objectName ?? "", date: new Date().toISOString().slice(0, 10) });
  const [rows, setRows] = useState<Row[]>([emptyRow()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setRow = (i: number, patch: Partial<Row>) => setRows((r) => r.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const setPair = (i: number, pair: string, value: string) =>
    setRows((r) => r.map((x, j) => (j === i ? { ...x, pairs: { ...x.pairs, [pair]: value } } : x)));

  async function download() {
    setBusy(true);
    setError("");
    const payload = {
      header,
      rows: rows.map((r) => ({
        line_name: r.line_name, cable_mark: r.cable_mark, length_m: r.length_m, voltage_v: r.voltage_v,
        pairs: Object.fromEntries(Object.entries(r.pairs).filter(([, v]) => v.trim() !== "")),
      })),
    };
    try {
      const response = await fetch(`/projects/${projectId}/executive-docs/insulation-protocol.xlsx`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let detail = `Не удалось сформировать протокол (${response.status})`;
        try { const body = await response.json(); if (body?.detail) detail = String(body.detail); } catch { /* keep default */ }
        throw new Error(detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Протокол изоляции.xlsx";
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
    <details className="id-insulation">
      <summary>Протокол измерения сопротивления изоляции (Excel)</summary>
      <p>Впишите результаты измерений мегаомметром в МОм. Заключение «годен / не годен» по норме система проставит сама.</p>
      <div className="id-form">
        {HEADER_FIELDS.map(([key, label]) => (
          <label key={key}>{label}
            <input value={header[key] ?? ""} onChange={(e) => setHeader((h) => ({ ...h, [key]: e.target.value }))} />
          </label>
        ))}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Линия</th><th>Кабель</th><th>Длина, м</th><th>Напряжение, В</th>
              {PAIRS.map((p) => <th key={p}>{p}</th>)}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input aria-label={`Линия ${i + 1}`} value={r.line_name} onChange={(e) => setRow(i, { line_name: e.target.value })} /></td>
                <td><input aria-label={`Кабель ${i + 1}`} value={r.cable_mark} onChange={(e) => setRow(i, { cable_mark: e.target.value })} /></td>
                <td><input aria-label={`Длина ${i + 1}`} inputMode="decimal" size={6} value={r.length_m} onChange={(e) => setRow(i, { length_m: e.target.value })} /></td>
                <td>
                  <select aria-label={`Напряжение ${i + 1}`} value={r.voltage_v} onChange={(e) => setRow(i, { voltage_v: e.target.value })}>
                    <option value="500">500</option><option value="1000">1000</option><option value="2500">2500</option>
                  </select>
                </td>
                {PAIRS.map((p) => (
                  <td key={p}><input aria-label={`${p} линия ${i + 1}`} inputMode="decimal" size={5} value={r.pairs[p] ?? ""} onChange={(e) => setPair(i, p, e.target.value)} /></td>
                ))}
                <td><button type="button" aria-label={`Убрать строку ${i + 1}`} onClick={() => setRows((x) => (x.length > 1 ? x.filter((_, j) => j !== i) : [emptyRow()]))}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <button type="button" onClick={() => setRows((r) => [...r, emptyRow()])}>Добавить линию</button>{" "}
        <button type="button" disabled={busy} onClick={() => void download()}>{busy ? "Формируем…" : "Скачать протокол"}</button>
      </p>
      {error && <p role="alert" className="id-missing">{error}</p>}
    </details>
  );
}
