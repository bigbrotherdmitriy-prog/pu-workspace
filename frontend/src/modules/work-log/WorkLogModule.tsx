import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import "./work-log.css";

type Entry = {
  id: number;
  work_date: string;
  object_name: string | null;
  work_name: string;
  unit: string | null;
  qty: string | null;
  location: string | null;
  weather: string | null;
  workers: number | null;
  note: string | null;
};

type Summary = { entries: number; days: number; by_work: { work_name: string; unit: string | null; qty_total: string; entries: number }[] };
type Suggestions = { works: { work_name: string; unit: string | null }[]; objects: string[]; rd_items: { id: number; name: string; unit: string | null }[] };
type ListResponse = { entries: Entry[]; summary: Summary };

const today = () => new Date().toISOString().slice(0, 10);

function formatDate(value: string): string {
  const [y, m, d] = value.split("-");
  return d && m && y ? `${d}.${m}.${y}` : value;
}

const emptyForm = () => ({ work_date: today(), object_name: "", work_name: "", unit: "", qty: "", location: "", weather: "", workers: "", note: "" });

export function WorkLogModule({ projectId }: { projectId: number }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [suggest, setSuggest] = useState<Suggestions>({ works: [], objects: [], rd_items: [] });
  const [form, setForm] = useState(emptyForm);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    setLoading(true);
    const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
    api<ListResponse>(`/projects/${projectId}/work-log${suffix}`)
      .then((res) => { if (alive) { setEntries(res?.entries ?? []); setSummary(res?.summary ?? null); setError(""); } })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : "Не удалось загрузить журнал"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [projectId, query, tick]);

  useEffect(() => {
    if (projectId <= 0) return;
    api<Suggestions>(`/projects/${projectId}/work-log/suggestions`)
      .then((res) => { if (res) setSuggest(res); })
      .catch(() => undefined);
  }, [projectId, tick]);

  const set = (field: keyof ReturnType<typeof emptyForm>, value: string) => setForm((f) => ({ ...f, [field]: value }));

  function pickWork(name: string) {
    const known = suggest.works.find((w) => w.work_name === name) ?? suggest.rd_items.find((i) => i.name === name);
    setForm((f) => ({ ...f, work_name: name, unit: f.unit || (known?.unit ?? "") }));
  }

  async function save() {
    if (!form.work_name.trim()) { setError("Укажите, какая работа выполнена"); return; }
    setSaving(true);
    setError("");
    setMessage("");
    const body: Record<string, unknown> = { work_date: form.work_date, work_name: form.work_name.trim() };
    for (const key of ["object_name", "unit", "qty", "location", "weather", "note"] as const) {
      if (form[key].trim()) body[key] = form[key].trim();
    }
    if (form.workers.trim()) body.workers = Number(form.workers);
    try {
      await api(`/projects/${projectId}/work-log`, { method: "POST", body: JSON.stringify(body) });
      setMessage("Запись добавлена");
      setForm((f) => ({ ...emptyForm(), work_date: f.work_date, object_name: f.object_name, location: f.location }));
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить запись");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    setError("");
    try {
      await api(`/projects/${projectId}/work-log/${id}`, { method: "DELETE" });
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить запись");
    }
  }

  async function downloadExcel() {
    try {
      const response = await fetch(`/projects/${projectId}/work-log/export.xlsx`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Журнал работ.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать файл");
    }
  }

  return (
    <div className="wl">
      <div className="wl-head">
        <h3 className="wl-title">Журнал работ</h3>
        <button type="button" onClick={() => void downloadExcel()}>Скачать в Excel</button>
      </div>

      <form className="wl-form" onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Новая запись журнала">
        <label>Дата<input type="date" value={form.work_date} onChange={(e) => set("work_date", e.target.value)} /></label>
        <label>Объект
          <input list="wl-objects" value={form.object_name} onChange={(e) => set("object_name", e.target.value)} placeholder="Например, ДУБНА" />
          <datalist id="wl-objects">{suggest.objects.map((o) => <option key={o} value={o} />)}</datalist>
        </label>
        <label className="wl-wide">Какая работа выполнена
          <input list="wl-works" value={form.work_name} onChange={(e) => pickWork(e.target.value)} placeholder="Начните вводить или выберите из спецификации" />
          <datalist id="wl-works">
            {suggest.works.map((w) => <option key={`w${w.work_name}`} value={w.work_name} />)}
            {suggest.rd_items.map((i) => <option key={`r${i.id}`} value={i.name} />)}
          </datalist>
        </label>
        <label>Объём<input inputMode="decimal" value={form.qty} onChange={(e) => set("qty", e.target.value)} placeholder="12,5" /></label>
        <label>Ед. изм.<input value={form.unit} onChange={(e) => set("unit", e.target.value)} placeholder="м, шт" /></label>
        <label>Место<input value={form.location} onChange={(e) => set("location", e.target.value)} placeholder="Ось, этаж, помещение" /></label>
        <label>Рабочих<input inputMode="numeric" value={form.workers} onChange={(e) => set("workers", e.target.value)} /></label>
        <label>Погода<input value={form.weather} onChange={(e) => set("weather", e.target.value)} placeholder="+5, дождь" /></label>
        <label className="wl-wide">Примечание<input value={form.note} onChange={(e) => set("note", e.target.value)} /></label>
        <button type="submit" className="wl-save" disabled={saving}>{saving ? "Сохраняем…" : "Добавить запись"}</button>
      </form>
      {message && <p className="wl-note" role="status">{message}</p>}
      {error && <p className="wl-error" role="alert">{error}</p>}

      {summary && summary.entries > 0 && (
        <p className="wl-note">Записей: {summary.entries}, рабочих дней: {summary.days}</p>
      )}
      <input className="wl-search" aria-label="Поиск по журналу" placeholder="Поиск по работам, месту, примечанию" value={query} onChange={(e) => setQuery(e.target.value)} />
      {loading && <p className="wl-note">Загружаем…</p>}
      {!loading && entries.length === 0 && <p className="wl-empty">Записей пока нет. Заполните форму выше: достаточно даты и названия работы.</p>}
      {entries.length > 0 && (
        <div className="wl-wrap">
          <table className="wl-table">
            <thead><tr><th>Дата</th><th>Объект</th><th>Работа</th><th>Объём</th><th>Место</th><th>Рабочих</th><th></th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{formatDate(e.work_date)}</td>
                  <td>{e.object_name || "—"}</td>
                  <td>{e.work_name}{e.note ? <div className="wl-sub">{e.note}</div> : null}</td>
                  <td>{e.qty ? `${e.qty} ${e.unit ?? ""}`.trim() : "—"}</td>
                  <td>{e.location || "—"}</td>
                  <td>{e.workers ?? "—"}</td>
                  <td><button type="button" aria-label={`Удалить запись ${e.work_name}`} onClick={() => void remove(e.id)}>Удалить</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
