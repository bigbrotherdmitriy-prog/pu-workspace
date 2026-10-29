import { useEffect, useState } from "react";
import { api } from "../../api/client";
import "./equipment.css";

type UnitStatus = "planned" | "received" | "installed" | "commissioned";

type Unit = {
  id: number;
  unit_no: number;
  name: string;
  type_mark: string | null;
  manufacturer: string | null;
  serial_number: string | null;
  location: string | null;
  status: UnitStatus;
  warranty_until: string | null;
  service_interval_months: number | null;
  next_service_date: string | null;
  service_overdue: boolean;
};

type Summary = {
  total: number;
  by_status: Record<UnitStatus, number>;
  with_serial: number;
  service_overdue: number;
};

type Response = { units: Unit[]; summary: Summary };

const statusLabel: Record<UnitStatus, string> = {
  planned: "Ожидается",
  received: "Получено",
  installed: "Смонтировано",
  commissioned: "Введено в работу",
};
const statuses = Object.keys(statusLabel) as UnitStatus[];
const emptySummary: Summary = {
  total: 0,
  by_status: { planned: 0, received: 0, installed: 0, commissioned: 0 },
  with_serial: 0,
  service_overdue: 0,
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  const [y, m, d] = value.split("-");
  return d && m && y ? `${d}.${m}.${y}` : value;
}

export function EquipmentModule({ projectId }: { projectId: number }) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [summary, setSummary] = useState<Summary>(emptySummary);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (query.trim()) params.set("q", query.trim());
    const suffix = params.toString() ? `?${params.toString()}` : "";
    setLoading(true);
    api<Response>(`/projects/${projectId}/equipment${suffix}`)
      .then((res) => {
        if (!alive) return;
        setUnits(Array.isArray(res?.units) ? res.units : []);
        setSummary(res?.summary ?? emptySummary);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "Не удалось загрузить оборудование");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [projectId, query, statusFilter, tick]);

  async function generate() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const res = await api<{ created: number; existing: number }>(`/projects/${projectId}/equipment/generate`, { method: "POST" });
      setMessage(`Создано: ${res?.created ?? 0}, уже было: ${res?.existing ?? 0}`);
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать список");
    } finally {
      setBusy(false);
    }
  }

  async function downloadExcel() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/projects/${projectId}/equipment/export.xlsx`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Оборудование.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать файл");
    } finally {
      setBusy(false);
    }
  }

  async function patch(id: number, body: Record<string, unknown>) {
    setError("");
    try {
      const updated = await api<Unit>(`/projects/${projectId}/equipment/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      setUnits((prev) => prev.map((u) => (u.id === id ? { ...u, ...updated } : u)));
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      setTick((v) => v + 1);
    }
  }

  const counters: [string, number][] = [
    ["Всего", summary.total],
    ...statuses.map((s): [string, number] => [statusLabel[s], summary.by_status?.[s] ?? 0]),
    ["С серийным №", summary.with_serial],
    ["ТО просрочено", summary.service_overdue],
  ];

  return (
    <div className="eq">
      <div className="eq-head">
        <h3 className="eq-title">Оборудование проекта</h3>
        <div className="eq-actions">
          <button type="button" disabled={busy} onClick={() => void generate()}>Создать из рабочей документации</button>
          <button type="button" disabled={busy} onClick={() => void downloadExcel()}>Скачать в Excel</button>
        </div>
      </div>
      {message && <p className="eq-note" role="status">{message}</p>}
      {error && <p className="eq-error" role="alert">{error}</p>}
      <div className="eq-counters">
        {counters.map(([label, value]) => (
          <span className="eq-counter" key={label}>{label}: <b>{value}</b></span>
        ))}
      </div>
      <div className="eq-filters">
        <input aria-label="Поиск оборудования" placeholder="Название, марка или заводской №" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select aria-label="Статус оборудования" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Все статусы</option>
          {statuses.map((s) => <option key={s} value={s}>{statusLabel[s]}</option>)}
        </select>
      </div>
      {loading && <p className="eq-note">Загружаем…</p>}
      {!loading && units.length === 0 && (
        <p className="eq-empty">Оборудования пока нет. Подтвердите позиции в «Рабочей документации» и нажмите «Создать из рабочей документации».</p>
      )}
      {units.length > 0 && (
        <div className="eq-wrap">
          <table className="eq-table">
            <thead>
              <tr>
                <th>Наименование</th><th>№</th><th>Заводской №</th><th>Место</th><th>Статус</th>
                <th>Гарантия до</th><th>ТО, мес.</th><th>Следующее ТО</th>
              </tr>
            </thead>
            <tbody>
              {units.map((u) => (
                <tr key={u.id}>
                  <td>
                    <b>{u.name}</b>
                    <div className="eq-sub">{[u.type_mark, u.manufacturer].filter(Boolean).join(" · ")}</div>
                  </td>
                  <td>{u.unit_no}</td>
                  <td>
                    <input
                      aria-label={`Заводской номер ${u.name} ${u.unit_no}`}
                      defaultValue={u.serial_number ?? ""}
                      key={`s${u.id}-${u.serial_number ?? ""}`}
                      onBlur={(e) => { if (e.target.value.trim() !== (u.serial_number ?? "")) void patch(u.id, { serial_number: e.target.value.trim() }); }}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`Место ${u.name} ${u.unit_no}`}
                      defaultValue={u.location ?? ""}
                      key={`l${u.id}-${u.location ?? ""}`}
                      onBlur={(e) => { if (e.target.value.trim() !== (u.location ?? "")) void patch(u.id, { location: e.target.value.trim() }); }}
                    />
                  </td>
                  <td>
                    <select aria-label={`Статус ${u.name} ${u.unit_no}`} value={u.status} onChange={(e) => void patch(u.id, { status: e.target.value })}>
                      {statuses.map((s) => <option key={s} value={s}>{statusLabel[s]}</option>)}
                    </select>
                  </td>
                  <td>
                    <input
                      type="date"
                      aria-label={`Гарантия до ${u.name} ${u.unit_no}`}
                      value={u.warranty_until ?? ""}
                      onChange={(e) => void patch(u.id, { warranty_until: e.target.value || null })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      max={120}
                      aria-label={`Интервал ТО ${u.name} ${u.unit_no}`}
                      defaultValue={u.service_interval_months ?? ""}
                      key={`i${u.id}-${u.service_interval_months ?? ""}`}
                      onBlur={(e) => {
                        const next = e.target.value ? Number(e.target.value) : null;
                        if (next !== u.service_interval_months) void patch(u.id, { service_interval_months: next });
                      }}
                    />
                  </td>
                  <td>
                    {formatDate(u.next_service_date)}
                    {u.service_overdue && <span className="eq-overdue"> просрочено</span>}
                    {u.service_interval_months ? (
                      <div>
                        <button type="button" aria-label={`ТО выполнено ${u.name} ${u.unit_no}`} onClick={() => void patch(u.id, { last_service_date: new Date().toISOString().slice(0, 10) })}>ТО выполнено сегодня</button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
