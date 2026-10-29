import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import "./rd.css";

type VolumeStatus = "new" | "parsing" | "parsed" | "failed";
type VolumeCounts = { proposed: number; confirmed: number; rejected: number };
type Volume = {
  id: number;
  title: string;
  file_name: string;
  section: string | null;
  code: string | null;
  object_name: string | null;
  status: VolumeStatus;
  page_count: number | null;
  spec_pages: string | null;
  error_message: string | null;
  counts: VolumeCounts;
};

type ItemStatus = "proposed" | "confirmed" | "rejected";
type Item = {
  id: number;
  volume_id: number;
  sort_order: number;
  pos: string | null;
  name: string | null;
  type_mark: string | null;
  unit: string | null;
  qty: string | null;
  note: string | null;
  source_page: number | null;
  status: ItemStatus;
};

type Props = { projectId: number };

const statusLabel: Record<VolumeStatus, string> = {
  new: "ждёт",
  parsing: "разбирается",
  parsed: "разобран",
  failed: "ошибка",
};
const statusClass: Record<VolumeStatus, string> = {
  new: "rd-status-new",
  parsing: "rd-status-parsing",
  parsed: "rd-status-parsed",
  failed: "rd-status-failed",
};

const itemStatusLabel: Record<ItemStatus, string> = {
  proposed: "на проверке",
  confirmed: "подтверждено",
  rejected: "отклонено",
};

const folderIdFromInput = (raw: string): string => {
  const value = raw.trim();
  if (!value) return "";
  const match = value.match(/\/folders\/([A-Za-z0-9_-]+)/);
  if (match) return match[1];
  return value;
};

const itemFieldValue = (item: Item, field: EditableField): string => {
  const value = item[field];
  return value == null ? "" : String(value);
};

type EditableField = "pos" | "name" | "type_mark" | "unit" | "qty" | "note";

export function RdModule({ projectId }: Props) {
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [folderInput, setFolderInput] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [importError, setImportError] = useState("");
  const [reloadTick, setReloadTick] = useState(0);
  const [parseBusy, setParseBusy] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const timerRef = useRef<number | null>(null);

  const openVolume = useMemo(() => volumes.find((volume) => volume.id === openId) || null, [volumes, openId]);

  useEffect(() => {
    if (projectId <= 0) { setVolumes([]); return; }
    let alive = true;
    const controller = new AbortController();
    setListLoading(true);
    api<Volume[]>(`/projects/${projectId}/rd/volumes`, { signal: controller.signal })
      .then((response) => {
        if (!alive) return;
        setVolumes(Array.isArray(response) ? response : []);
        setListError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!alive) return;
        setListError(err instanceof Error ? err.message : "Не удалось загрузить тома");
      })
      .finally(() => { if (alive) setListLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [projectId, reloadTick]);

  const pollActive = volumes.some((volume) => volume.status === "new" || volume.status === "parsing");
  useEffect(() => {
    if (!pollActive || projectId <= 0) return;
    const id = window.setInterval(() => { setReloadTick((value) => value + 1); }, 5000);
    timerRef.current = id;
    return () => {
      window.clearInterval(id);
      if (timerRef.current === id) timerRef.current = null;
    };
  }, [pollActive, projectId]);

  async function importDocs() {
    const folderId = folderIdFromInput(folderInput);
    if (!folderId) return;
    setImportBusy(true);
    setImportError("");
    setImportMessage("");
    try {
      const result = await api<{ added: number; existing: number; volumes: Volume[] }>(
        `/projects/${projectId}/rd/import`,
        { method: "POST", body: JSON.stringify({ folder_id: folderId }) },
      );
      setImportMessage(`Добавлено: ${result.added}, уже было: ${result.existing}`);
      setFolderInput("");
      setReloadTick((value) => value + 1);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Не удалось загрузить тома");
    } finally {
      setImportBusy(false);
    }
  }

  async function reparse(volume: Volume) {
    setParseBusy(volume.id);
    setListError("");
    try {
      await api(`/projects/${projectId}/rd/volumes/${volume.id}/parse`, { method: "POST" });
      setReloadTick((value) => value + 1);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Не удалось перезапустить разбор");
    } finally {
      setParseBusy(null);
    }
  }

  if (openVolume) {
    return (
      <VolumeItems
        projectId={projectId}
        volume={openVolume}
        onBack={() => { setOpenId(null); setReloadTick((value) => value + 1); }}
      />
    );
  }

  return (
    <div className="rd">
      <div className="rd-import">
        <label htmlFor="rd-folder" className="rd-import-label">Ссылка или ID папки на Google Диске</label>
        <div className="rd-import-row">
          <input
            id="rd-folder"
            type="text"
            value={folderInput}
            onChange={(event) => setFolderInput(event.target.value)}
            placeholder="https://drive.google.com/drive/folders/…"
          />
          <button type="button" disabled={importBusy || !folderIdFromInput(folderInput)} onClick={() => void importDocs()}>Загрузить тома</button>
        </div>
        {importMessage && <div className="rd-import-ok">{importMessage}</div>}
        {importError && <div className="rd-error" role="alert">{importError}</div>}
      </div>

      {listError && <div className="rd-error" role="alert">{listError}</div>}

      {volumes.length === 0 ? (
        <div className="rd-empty">
          {listLoading ? "Загружаем тома…" : "Томов пока нет. Вставьте ссылку на папку с рабочей документацией на Google Диске"}
        </div>
      ) : (
        <div className="rd-table">
          <table>
            <thead>
              <tr>
                <th>Раздел</th>
                <th>Название</th>
                <th>Страниц</th>
                <th>Страницы спецификации</th>
                <th>Статус</th>
                <th>Позиций</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {volumes.map((volume) => {
                const proposed = volume.counts?.proposed ?? 0;
                const confirmed = volume.counts?.confirmed ?? 0;
                return (
                  <tr key={volume.id}>
                    <td>{volume.section || "—"}</td>
                    <td>
                      <div className="rd-title">{volume.title || volume.file_name}</div>
                      {volume.code && <div className="rd-sub">{volume.code}</div>}
                      {volume.status === "failed" && volume.error_message && (
                        <div className="rd-row-error" role="alert">{volume.error_message}</div>
                      )}
                    </td>
                    <td className="rd-num">{volume.page_count == null ? "—" : volume.page_count}</td>
                    <td>{volume.spec_pages || "—"}</td>
                    <td>
                      <span className={`rd-status ${statusClass[volume.status] || "rd-status-new"}`}>{statusLabel[volume.status] || "ждёт"}</span>
                    </td>
                    <td className="rd-num">{proposed} / {confirmed}</td>
                    <td className="rd-action">
                      <button type="button" onClick={() => setOpenId(volume.id)}>Открыть</button>
                      <button type="button" disabled={parseBusy === volume.id} onClick={() => void reparse(volume)}>Разобрать заново</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type ItemsProps = {
  projectId: number;
  volume: Volume;
  onBack: () => void;
};

function VolumeItems({ projectId, volume, onBack }: ItemsProps) {
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [reload, setReload] = useState(0);
  const [editing, setEditing] = useState<{ id: number; field: EditableField; value: string } | null>(null);
  const [busy, setBusy] = useState<number | "all" | null>(null);

  useEffect(() => {
    if (projectId <= 0) { setItems([]); return; }
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    api<Item[]>(`/projects/${projectId}/rd/volumes/${volume.id}/items`, { signal: controller.signal })
      .then((response) => {
        if (!alive) return;
        setItems(Array.isArray(response) ? response : []);
        setError("");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить позиции");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [projectId, volume.id, reload]);

  const proposedCount = items.filter((item) => item.status === "proposed").length;
  const confirmedCount = items.filter((item) => item.status === "confirmed").length;

  async function patchItem(id: number, patch: Record<string, unknown>) {
    setBusy(id);
    setRowError(null);
    try {
      await api(`/projects/${projectId}/rd/items/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      setReload((value) => value + 1);
    } catch (err) {
      setRowError({ id, message: err instanceof Error ? err.message : "Не удалось сохранить позицию" });
    } finally {
      setBusy(null);
    }
  }

  async function confirmAll() {
    setBusy("all");
    setError("");
    try {
      await api(`/projects/${projectId}/rd/volumes/${volume.id}/confirm-all`, { method: "POST" });
      setReload((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подтвердить всё");
    } finally {
      setBusy(null);
    }
  }

  function commitEdit(item: Item) {
    if (!editing || editing.id !== item.id) return;
    const original = itemFieldValue(item, editing.field);
    if (editing.value === original) { setEditing(null); return; }
    const patch: Record<string, unknown> = {};
    patch[editing.field] = editing.value;
    setEditing(null);
    void patchItem(item.id, patch);
  }

  function startEdit(item: Item, field: EditableField) {
    setEditing({ id: item.id, field, value: itemFieldValue(item, field) });
  }

  function cellInput(item: Item, field: EditableField, aria: string, numeric = false) {
    const isActive = editing?.id === item.id && editing.field === field;
    const value = isActive ? editing.value : itemFieldValue(item, field);
    if (isActive) {
      return (
        <input
          className={`rd-cell-input${numeric ? " rd-cell-num" : ""}`}
          aria-label={aria}
          value={editing.value}
          autoFocus
          onChange={(event) => setEditing({ ...editing, value: event.target.value })}
          onBlur={() => commitEdit(item)}
          onKeyDown={(event) => {
            if (event.key === "Enter") { event.preventDefault(); commitEdit(item); }
            if (event.key === "Escape") { event.preventDefault(); setEditing(null); }
          }}
        />
      );
    }
    return (
      <span
        className={`rd-cell${numeric ? " rd-cell-num" : ""}`}
        role="button"
        tabIndex={0}
        onClick={() => startEdit(item, field)}
        onKeyDown={(event) => { if (event.key === "Enter") startEdit(item, field); }}
      >
        {value || "—"}
      </span>
    );
  }

  return (
    <div className="rd">
      <div className="rd-head">
        <button type="button" onClick={onBack}>Назад к томам</button>
        <div className="rd-head-title">{volume.title || volume.file_name}</div>
        <div className="rd-counters">на проверке: {proposedCount} · подтверждено: {confirmedCount}</div>
        <button type="button" disabled={busy === "all" || proposedCount === 0} onClick={() => void confirmAll()}>Подтвердить всё</button>
      </div>

      {error && <div className="rd-error" role="alert">{error}</div>}

      {items.length === 0 && !loading ? (
        <div className="rd-empty">Позиций пока нет</div>
      ) : (
        <div className="rd-table">
          <table>
            <thead>
              <tr>
                <th>Поз.</th>
                <th>Наименование</th>
                <th>Тип/марка</th>
                <th>Ед.</th>
                <th>Кол-во</th>
                <th>Лист</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const rejected = item.status === "rejected";
                return (
                  <tr key={item.id} className={rejected ? "rd-row-rejected" : undefined}>
                    <td>{cellInput(item, "pos", "Позиция")}</td>
                    <td>{cellInput(item, "name", "Наименование")}</td>
                    <td>{cellInput(item, "type_mark", "Тип или марка")}</td>
                    <td>{cellInput(item, "unit", "Единица")}</td>
                    <td>{cellInput(item, "qty", "Количество", true)}</td>
                    <td className="rd-num">{item.source_page == null ? "—" : item.source_page}</td>
                    <td><span className={`rd-item-status rd-item-status-${item.status}`}>{itemStatusLabel[item.status] || item.status}</span></td>
                    <td className="rd-action">
                      <button
                        type="button"
                        disabled={busy === item.id || item.status === "confirmed"}
                        onClick={() => void patchItem(item.id, { status: "confirmed" })}
                      >Подтвердить</button>
                      <button
                        type="button"
                        disabled={busy === item.id || item.status === "rejected"}
                        onClick={() => void patchItem(item.id, { status: "rejected" })}
                      >Отклонить</button>
                      {rowError?.id === item.id && <span className="rd-row-error" role="alert">{rowError.message}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rowError && !items.some((item) => item.id === rowError.id) && (
        <div className="rd-error" role="alert">{rowError.message}</div>
      )}
    </div>
  );
}

export default RdModule;
