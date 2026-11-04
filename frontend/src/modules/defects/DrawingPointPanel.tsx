import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { captureOfflineSession, requireOfflineSession } from "../../offline/lifecycle";

export type DrawingPoint = { rd_version_id: number; page: number; x: string; y: string; coordinate_system: string };
type Version = { id: number; title: string; file_name: string; content_sha256: string; created_at: string | null };
type Page = { versions: Version[]; next_before_id: number | null; metadata_only: boolean };
export function pointDraft(version: number, page: string, x: string, y: string) {
  if (!Number.isSafeInteger(version) || version <= 0 || !/^[1-9]\d{0,9}$/.test(page) || Number(page) > 2147483647) throw new Error("Выберите версию и номер страницы от 1");
  const coordinate = (value: string) => {
    const normalized = value.trim().replace(",", ".");
    if (!/^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$/.test(normalized)) throw new Error("Координаты от 0 до 1, до 6 знаков после запятой");
    return Number(normalized);
  };
  return { rd_version_id: version, page: Number(page), x: coordinate(x), y: coordinate(y) };
}

export function DrawingPointPanel({ projectId, userId, defectId, boundVersionId, point, canEdit, onBound }: {
  projectId: number; userId: number; defectId: number; boundVersionId: number | null;
  point: DrawingPoint | null; canEdit: boolean; onBound: () => void;
}) {
  const [versions, setVersions] = useState<Version[]>([]); const [cursor, setCursor] = useState<number | null>(null);
  const [version, setVersion] = useState(boundVersionId || 0); const [page, setPage] = useState("");
  const [x, setX] = useState(""); const [y, setY] = useState("");
  const [error, setError] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false); const [loaded, setLoaded] = useState(false); const [tick, setTick] = useState(0);
  const [pending, setPending] = useState<ReturnType<typeof pointDraft> | null>(null);
  const scope = useRef<object>({}); const inFlight = useRef(false);
  useEffect(() => {
    const token = {}; scope.current = token; inFlight.current = false;
    setVersions([]); setCursor(null); setLoaded(false); setVersion(boundVersionId || 0); setPage(""); setX(""); setY(""); setError(""); setMessage(""); setPending(null); setBusy(false);
    return () => { if (scope.current === token) scope.current = {}; };
  }, [projectId, userId, defectId, boundVersionId]);
  useEffect(() => {
    if (point || !canEdit) return;
    const token = scope.current; let alive = true; setLoading(true);
    api<Page>(`/projects/${projectId}/rd/drawing-versions?limit=25`).then(result => {
      if (!alive || scope.current !== token) return;
      if (!Array.isArray(result.versions) || result.metadata_only !== true) throw new Error("Не подтверждён список версий");
      setVersions(result.versions); setCursor(result.next_before_id); setLoaded(true);
    }).catch(reason => { if (alive && scope.current === token) setError((reason as Error).message); })
      .finally(() => { if (alive && scope.current === token) setLoading(false); });
    return () => { alive = false; };
  }, [projectId, userId, defectId, boundVersionId, canEdit, point, tick]);
  async function more() {
    if (!cursor || loading) return;
    const token = scope.current; setLoading(true);
    try {
      const result = await api<Page>(`/projects/${projectId}/rd/drawing-versions?limit=25&before_id=${cursor}`);
      if (scope.current !== token) return;
      if (!Array.isArray(result.versions) || result.metadata_only !== true) throw new Error("Не подтверждён список версий");
      setVersions(current => [...current, ...result.versions.filter(row => !current.some(old => old.id === row.id))]); setCursor(result.next_before_id);
    } catch (reason) { if (scope.current === token) setError((reason as Error).message); }
    finally { if (scope.current === token) setLoading(false); }
  }
  async function save() {
    if (inFlight.current || !canEdit || point) return;
    const token = scope.current; inFlight.current = true; setBusy(true); setError("");
    try {
      const session = captureOfflineSession(userId);
      if (userId <= 0) throw new Error("Войдите заново");
      const intent = pending ?? pointDraft(version, page, x, y);
      if (!pending && !versions.some(row => row.id === intent.rd_version_id)) throw new Error("Выберите версию из списка проекта");
      if (!pending && !window.confirm("Привязка к версии и точке неизменяема. Страница PDF автоматически не проверялась. Сохранить указанное место?")) return;
      setPending(intent);
      const result = await api<DrawingPoint>(`/projects/${projectId}/defects/${defectId}/drawing-point`, { method: "POST", headers: { "X-PU-Expected-User": String(userId) }, body: JSON.stringify(intent) });
      requireOfflineSession(session); if (scope.current !== token) return;
      if (!result || typeof result.x !== "string" || typeof result.y !== "string" || !result.x.trim() || !result.y.trim() || result.rd_version_id !== intent.rd_version_id || result.page !== intent.page || Number(result.x) !== intent.x || Number(result.y) !== intent.y || result.coordinate_system !== "normalized_page_v1") throw new Error("Привязка не подтверждена; повторите исходные координаты");
      setMessage("Привязка подтверждена. Автоматического переноса на новую версию нет."); setPending(null); onBound();
    } catch (reason) { if (scope.current === token) setError((reason as Error).message); }
    finally { if (scope.current === token) { inFlight.current = false; setBusy(false); } }
  }
  return <section aria-label="Место на чертеже"><h5>Место на чертеже</h5>
    {point ? <p>Версия № {point.rd_version_id}, страница {point.page}, x={point.x}, y={point.y}. Привязка неизменяема; на новую версию не переносится.</p>
      : !canEdit ? <p>Точка пока не указана.</p> : <>
        <p>Указатель по метаданным, без просмотра PDF. Координаты от левого верхнего угла: 0 — начало, 1 — край. Число страниц автоматически не проверялось. Сверьте исходный файл перед сохранением.</p>
        {boundVersionId && <p>Замечание уже связано с версией № {boundVersionId}; другая версия недоступна.</p>}
        <label>Версия чертежа<select value={version || ""} disabled={busy || !!pending || !!boundVersionId} onChange={event => setVersion(Number(event.target.value))}>
          <option value="">Выберите версию</option>{versions.filter(row => !boundVersionId || row.id === boundVersionId).map(row => <option key={row.id} value={row.id}>{row.title} · {row.file_name} · SHA {row.content_sha256.slice(0, 12)} · версия {row.id}</option>)}
        </select></label>
        {cursor && <button type="button" disabled={loading || busy} onClick={() => void more()}>Загрузить предыдущие версии</button>}
        {loaded && !versions.length && <p>Сохранённых версий нет. Версию из внешнего URL не подставляем.</p>}
        {!loaded && !loading && <button type="button" onClick={() => setTick(value => value + 1)}>Повторить загрузку версий</button>}
        <label>Страница PDF<input inputMode="numeric" value={page} maxLength={10} disabled={busy || !!pending} onChange={event => setPage(event.target.value)} /></label>
        <label>Координата x<input inputMode="decimal" value={x} maxLength={8} disabled={busy || !!pending} onChange={event => setX(event.target.value)} /></label>
        <label>Координата y<input inputMode="decimal" value={y} maxLength={8} disabled={busy || !!pending} onChange={event => setY(event.target.value)} /></label>
        <button type="button" disabled={busy || !loaded} onClick={() => void save()}>{pending ? "Повторить исходную привязку" : "Привязать точку"}</button>
        {pending && <p>Исходные координаты зафиксированы для повтора в этой вкладке. После перезагрузки сначала проверьте сохранённую привязку.</p>}
      </>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
