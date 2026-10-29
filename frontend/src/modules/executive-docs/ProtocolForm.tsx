import { useState } from "react";

export type ProtocolColumn = { key: string; label: string; options?: [string, string][]; size?: number; placeholder?: string; default?: string };
export type ProtocolConfig = {
  id: string;
  summary: string;
  hint: string;
  endpoint: string;
  filename: string;
  headerFields: [string, string][];
  columns: ProtocolColumn[];
};

const defaultRow = (cfg: ProtocolConfig): Record<string, string> =>
  Object.fromEntries(cfg.columns.map((c) => [c.key, c.default ?? ""]));

export function ProtocolForm({ projectId, objectName, config }: { projectId: number; objectName?: string; config: ProtocolConfig }) {
  const [header, setHeader] = useState<Record<string, string>>({ object_name: objectName ?? "", date: new Date().toISOString().slice(0, 10) });
  const [rows, setRows] = useState<Record<string, string>[]>([defaultRow(config)]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setCell = (i: number, key: string, value: string) => setRows((r) => r.map((x, j) => (j === i ? { ...x, [key]: value } : x)));

  async function download() {
    setBusy(true);
    setError("");
    const payload = {
      header,
      rows: rows
        .filter((r) => config.columns.some((c) => r[c.key].trim() !== "" && r[c.key] !== (c.default ?? "")))
        .map((r) => Object.fromEntries(Object.entries(r).filter(([, v]) => v.trim() !== ""))),
    };
    try {
      const response = await fetch(`/projects/${projectId}/executive-docs/${config.endpoint}`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!response.ok) {
        let detail = `Не удалось сформировать протокол (${response.status})`;
        try { const body = await response.json(); if (body?.detail) detail = String(body.detail); } catch { /* keep default */ }
        throw new Error(detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = config.filename;
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
    <details className={`id-protocol id-protocol-${config.id}`}>
      <summary>{config.summary}</summary>
      <p>{config.hint}</p>
      <div className="id-form">
        {config.headerFields.map(([key, label]) => (
          <label key={key}>{label}
            <input type={key === "date" ? "date" : "text"} value={header[key] ?? ""} onChange={(e) => setHeader((h) => ({ ...h, [key]: e.target.value }))} />
          </label>
        ))}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr>{config.columns.map((c) => <th key={c.key}>{c.label}</th>)}<th></th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {config.columns.map((c) => (
                  <td key={c.key}>
                    {c.options ? (
                      <select aria-label={`${c.label} ${i + 1}`} value={r[c.key]} onChange={(e) => setCell(i, c.key, e.target.value)}>
                        {c.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    ) : (
                      <input aria-label={`${c.label} ${i + 1}`} size={c.size} placeholder={c.placeholder} value={r[c.key]} onChange={(e) => setCell(i, c.key, e.target.value)} />
                    )}
                  </td>
                ))}
                <td><button type="button" aria-label={`Убрать строку ${i + 1}`} onClick={() => setRows((x) => (x.length > 1 ? x.filter((_, j) => j !== i) : [defaultRow(config)]))}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        <button type="button" onClick={() => setRows((r) => [...r, defaultRow(config)])}>Добавить строку</button>{" "}
        <button type="button" disabled={busy} onClick={() => void download()}>{busy ? "Формируем…" : "Скачать протокол"}</button>
      </p>
      {error && <p role="alert" className="id-missing">{error}</p>}
    </details>
  );
}

const COMMON_HEADER: [string, string][] = [
  ["object_name", "Объект"], ["address", "Адрес"], ["customer", "Заказчик"], ["contractor", "Подрядчик"],
  ["laboratory", "Лаборатория / исполнитель"], ["protocol_no", "№ протокола"], ["date", "Дата измерений"],
  ["instrument", "Прибор (тип, №)"], ["instrument_check_date", "Поверка до"], ["temperature", "Температура, °C"],
  ["tested_by", "Измерения выполнил"], ["checked_by", "Проверил"],
];

export const EARTHING_PROTOCOL: ProtocolConfig = {
  id: "earthing",
  summary: "Протокол измерения сопротивления заземляющего устройства (Excel)",
  hint: "Впишите измеренное сопротивление в Омах. Приведённое значение и заключение «годен / не годен» по норме система посчитает сама.",
  endpoint: "earthing-protocol.xlsx",
  filename: "Протокол заземления.xlsx",
  headerFields: [...COMMON_HEADER, ["soil_state", "Состояние грунта"]],
  columns: [
    { key: "point_name", label: "Заземляющее устройство / точка", size: 18 },
    { key: "location", label: "Место", size: 12 },
    { key: "soil", label: "Грунт", size: 10 },
    { key: "electrode", label: "Заземлитель", size: 12 },
    { key: "network_voltage_v", label: "Напряжение сети, В", default: "380", options: [["380", "380"], ["220", "220"], ["660", "660"]] },
    { key: "measured_ohm", label: "Измерено, Ом", size: 6 },
    { key: "season_factor", label: "Коэф. сезонности", size: 5, placeholder: "1,0" },
  ],
};

export const PHASE_ZERO_PROTOCOL: ProtocolConfig = {
  id: "phase-zero",
  summary: "Протокол проверки цепи «фаза–нуль» (Excel)",
  hint: "Впишите измеренное сопротивление петли «фаза–нуль» (Ом) или ток КЗ (А). Система проверит, отключит ли аппарат цепь при коротком замыкании.",
  endpoint: "phase-zero-protocol.xlsx",
  filename: "Протокол фаза-нуль.xlsx",
  headerFields: COMMON_HEADER,
  columns: [
    { key: "circuit_name", label: "Цепь / щит / приёмник", size: 18 },
    { key: "device_type", label: "Аппарат", default: "автоматический выключатель", options: [["автоматический выключатель", "Автомат"], ["предохранитель", "Предохранитель"], ["УЗО", "УЗО"]] },
    { key: "device_mark", label: "Марка", size: 10 },
    { key: "rated_a", label: "Iном, А", size: 5 },
    { key: "characteristic", label: "Характеристика", default: "", options: [["", "—"], ["B", "B"], ["C", "C"], ["D", "D"]] },
    { key: "phase_voltage_v", label: "Uф, В", size: 4, placeholder: "220" },
    { key: "loop_ohm", label: "Zпц, Ом", size: 6 },
    { key: "short_circuit_a", label: "Iкз, А (если измерен)", size: 7 },
  ],
};

export const AVR_PROTOCOL: ProtocolConfig = {
  id: "avr",
  summary: "Протокол проверки устройства АВР (Excel)",
  hint: "Впишите сценарии проверки: пропадание напряжения на вводе, возврат и т. п. Время переключения сравнивается с допустимым, заключение составится само.",
  endpoint: "avr-protocol.xlsx",
  filename: "Протокол АВР.xlsx",
  headerFields: [
    ["object_name", "Объект"], ["address", "Адрес"], ["customer", "Заказчик"], ["contractor", "Подрядчик"],
    ["laboratory", "Лаборатория / исполнитель"], ["protocol_no", "№ протокола"], ["date", "Дата проверки"],
    ["device", "Устройство АВР (тип, шкаф)"], ["instrument", "Секундомер / осциллограф (тип, №)"], ["instrument_check_date", "Поверка до"],
    ["input1", "Ввод 1 (источник, напряжение)"], ["input2", "Ввод 2 (источник, напряжение)"], ["tested_by", "Проверку выполнил"], ["checked_by", "Проверил"],
  ],
  columns: [
    { key: "scenario", label: "Сценарий", size: 26, placeholder: "Пропадание напряжения на вводе 1" },
    { key: "expected", label: "Ожидаемое действие", size: 22, placeholder: "Переход на ввод 2" },
    { key: "measured_time_s", label: "Измерено, с", size: 5 },
    { key: "allowed_time_s", label: "Допустимо, с", size: 5, placeholder: "1,0" },
    { key: "action_ok", label: "Сработало как ожидалось", default: "", options: [["", "—"], ["yes", "Да"], ["no", "Нет"]] },
  ],
};
