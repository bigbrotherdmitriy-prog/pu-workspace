import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Catalog = { kind: string; title: string; certificate: string };
type Cert = { id: number; filename: string; mime_type: string | null; size_bytes: number };
type Order = {
  id: number; kind: string; title: string; certificate_required: string; number: string | null; order_date: string | null;
  person_name: string | null; position: string | null; org_name: string | null; object_name: string | null; city: string | null;
  signer_name: string | null; signer_position: string | null; extra: string | null;
  certificates: Cert[]; complete: boolean; missing: string[];
};

const defaultsKey = (projectId: number) => `pu-id-orders-defaults:${projectId}`;
type Defaults = { org_name: string; object_name: string; city: string; signer_name: string; signer_position: string };
const emptyDefaults: Defaults = { org_name: "", object_name: "", city: "", signer_name: "", signer_position: "Генеральный директор" };

function readDefaults(projectId: number): Defaults {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(defaultsKey(projectId)) || "null");
    if (value && typeof value === "object") return { ...emptyDefaults, ...(value as Partial<Defaults>) };
  } catch { /* defaults are optional */ }
  return emptyDefaults;
}

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

export function IdOrders({ projectId }: { projectId: number }) {
  const [catalog, setCatalog] = useState<Catalog[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [defaults, setDefaults] = useState<Defaults>(() => readDefaults(projectId));
  const [draft, setDraft] = useState({ kind: "labor_safety", person_name: "", position: "", number: "", order_date: new Date().toISOString().slice(0, 10) });

  const load = useCallback(async () => {
    try {
      const res = await api<{ catalog: Catalog[]; orders: Order[] }>(`/projects/${projectId}/id-orders`);
      setCatalog(res?.catalog ?? []);
      setOrders(res?.orders ?? []);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const setDefault = (field: keyof Defaults, value: string) => {
    const next = { ...defaults, [field]: value };
    setDefaults(next);
    try { localStorage.setItem(defaultsKey(projectId), JSON.stringify(next)); } catch { /* keep working without memory */ }
  };

  async function create() {
    setError("");
    setMessage("");
    try {
      await api(`/projects/${projectId}/id-orders`, { method: "POST", body: JSON.stringify({ ...draft, ...defaults }) });
      setDraft((d) => ({ ...d, person_name: "", position: "", number: "" }));
      setMessage("Приказ добавлен. Теперь загрузите копию удостоверения ответственного.");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function patch(id: number, body: Record<string, unknown>) {
    try {
      await api(`/projects/${projectId}/id-orders/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(id: number) {
    try {
      await api(`/projects/${projectId}/id-orders/${id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function uploadCert(order: Order, files: File[]) {
    setError("");
    for (const file of files) {
      try {
        const response = await fetch(`/projects/${projectId}/id-orders/${order.id}/certificate?name=${encodeURIComponent(file.name)}`, {
          method: "POST", body: file, credentials: "same-origin", headers: { "Content-Type": file.type || "application/octet-stream" },
        });
        if (!response.ok) {
          let detail = `Не удалось загрузить ${file.name} (${response.status})`;
          try { const body = await response.json(); if (body?.detail) detail = String(body.detail); } catch { /* keep default */ }
          throw new Error(detail);
        }
      } catch (e) {
        setError((e as Error).message);
      }
    }
    await load();
  }

  async function removeCert(order: Order, cert: Cert) {
    try {
      await api(`/projects/${projectId}/id-orders/${order.id}/certificate/${cert.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function download(path: string, filename: string) {
    setError("");
    try {
      const response = await fetch(`/projects/${projectId}${path}`, { credentials: "same-origin" });
      if (!response.ok) {
        let detail = `Не удалось скачать (${response.status})`;
        try { const body = await response.json(); if (body?.detail) detail = String(body.detail); } catch { /* keep default */ }
        throw new Error(detail);
      }
      await saveBlob(response, filename);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const incomplete = orders.filter((o) => !o.complete).length;

  return (
    <details className="id-orders">
      <summary>Приказы на ответственных лиц (охрана труда, огневые работы и др.)</summary>
      <p>Выберите вид приказа, впишите фамилию и должность: Word-документ сформируется сам. К каждому приказу обязательно прикладывается копия удостоверения ответственного.</p>

      <h4>Реквизиты для всех приказов</h4>
      <div className="id-form">
        <label>Организация<input value={defaults.org_name} onChange={(e) => setDefault("org_name", e.target.value)} /></label>
        <label>Объект<input value={defaults.object_name} onChange={(e) => setDefault("object_name", e.target.value)} /></label>
        <label>Город<input value={defaults.city} onChange={(e) => setDefault("city", e.target.value)} /></label>
        <label>Руководитель (ФИО)<input value={defaults.signer_name} onChange={(e) => setDefault("signer_name", e.target.value)} /></label>
        <label>Должность руководителя<input value={defaults.signer_position} onChange={(e) => setDefault("signer_position", e.target.value)} /></label>
      </div>

      <h4>Новый приказ</h4>
      <div className="id-form">
        <label>Вид приказа
          <select value={draft.kind} onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value }))}>
            {catalog.map((c) => <option key={c.kind} value={c.kind}>{c.title}</option>)}
          </select>
        </label>
        <label>ФИО ответственного<input value={draft.person_name} onChange={(e) => setDraft((d) => ({ ...d, person_name: e.target.value }))} placeholder="Иванов Иван Иванович" /></label>
        <label>Должность<input value={draft.position} onChange={(e) => setDraft((d) => ({ ...d, position: e.target.value }))} placeholder="прораб" /></label>
        <label>№ приказа<input value={draft.number} onChange={(e) => setDraft((d) => ({ ...d, number: e.target.value }))} /></label>
        <label>Дата<input type="date" value={draft.order_date} onChange={(e) => setDraft((d) => ({ ...d, order_date: e.target.value }))} /></label>
        <button type="button" onClick={() => void create()}>Добавить приказ</button>
      </div>
      {catalog.find((c) => c.kind === draft.kind) && <p>Потребуется: {catalog.find((c) => c.kind === draft.kind)?.certificate}.</p>}
      {message && <p role="status">{message}</p>}
      {error && <p role="alert" className="id-missing">{error}</p>}

      {orders.length === 0 && <p>Приказов пока нет.</p>}
      {orders.length > 0 && (
        <>
          <p>
            Приказов: {orders.length}, без копии удостоверения или ФИО: {incomplete}.{" "}
            <button type="button" onClick={() => void download("/id-orders/package.zip", "Приказы.zip")}>Скачать комплект (zip)</button>
          </p>
          <ul className="id-order-list">
            {orders.map((o) => (
              <li key={o.id} className="id-order">
                <b>{o.title}</b>
                <div>{o.person_name || "ФИО не указано"}{o.position ? `, ${o.position}` : ""}{o.number ? ` · № ${o.number}` : ""}{o.order_date ? ` · ${o.order_date.split("-").reverse().join(".")}` : ""}</div>
                {o.complete
                  ? <div role="status">Готов: копия удостоверения приложена</div>
                  : <div className="id-missing">Не хватает: {o.missing.join(", ")}. Нужно: {o.certificate_required}.</div>}
                <div className="id-order-actions">
                  <button type="button" onClick={() => void download(`/id-orders/${o.id}/order.docx`, "Приказ.docx")}>Скачать приказ (Word)</button>
                  <input id={`id-cert-${o.id}`} type="file" hidden multiple accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/*" aria-label={`Копия удостоверения для приказа ${o.id}`}
                    onChange={(e) => { void uploadCert(o, Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }} />
                  <button type="button" onClick={() => document.getElementById(`id-cert-${o.id}`)?.click()}>Загрузить копию удостоверения</button>
                  <button type="button" aria-label={`Удалить приказ ${o.id}`} onClick={() => void remove(o.id)}>Удалить приказ</button>
                </div>
                {o.certificates.length > 0 && (
                  <ul>
                    {o.certificates.map((c) => (
                      <li key={c.id}>
                        <button type="button" onClick={() => void download(`/id-orders/${o.id}/certificate/${c.id}`, c.filename)}>{c.filename}</button>{" "}
                        <button type="button" aria-label={`Убрать ${c.filename}`} onClick={() => void removeCert(o, c)}>×</button>
                      </li>
                    ))}
                  </ul>
                )}
                <details>
                  <summary>Изменить данные приказа</summary>
                  <div className="id-form">
                    <label>ФИО<input defaultValue={o.person_name ?? ""} onBlur={(e) => { if (e.target.value.trim() !== (o.person_name ?? "")) void patch(o.id, { person_name: e.target.value }); }} /></label>
                    <label>Должность<input defaultValue={o.position ?? ""} onBlur={(e) => { if (e.target.value.trim() !== (o.position ?? "")) void patch(o.id, { position: e.target.value }); }} /></label>
                    <label>№<input defaultValue={o.number ?? ""} onBlur={(e) => { if (e.target.value.trim() !== (o.number ?? "")) void patch(o.id, { number: e.target.value }); }} /></label>
                    <label>Дополнительный пункт<input defaultValue={o.extra ?? ""} onBlur={(e) => { if (e.target.value.trim() !== (o.extra ?? "")) void patch(o.id, { extra: e.target.value }); }} /></label>
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}
