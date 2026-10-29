import { useCallback, useEffect, useRef, useState } from "react";
import { csrfHeaders } from "../../api/client";
import { api } from "../../api/client";
import { LocalFolderSync } from "./LocalFolderSync";
import "./pu-storage.css";

type FileRow = { id: string; name: string; is_folder: boolean; size: number | null; modified_time: string | null; mime_type: string };
type Listing = { files: FileRow[]; usage: { used_bytes: number; quota_bytes: number } };

const ROOT = "pu:/";

function formatSize(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const [y, m, d] = value.slice(0, 10).split("-");
  return d && m && y ? `${d}.${m}.${y}` : value;
}

function crumbs(folder: string): { id: string; name: string }[] {
  const parts = folder.slice(ROOT.length).split("/").filter(Boolean);
  const result = [{ id: ROOT, name: "Хранилище" }];
  parts.forEach((part, i) => result.push({ id: ROOT + parts.slice(0, i + 1).join("/"), name: part }));
  return result;
}

export function PuStorageModule({ projectId }: { projectId: number }) {
  const [folder, setFolder] = useState(ROOT);
  const [listing, setListing] = useState<Listing | null>(null);
  const [connected, setConnected] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [progress, setProgress] = useState("");
  const [newFolder, setNewFolder] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (projectId <= 0) return;
    let alive = true;
    setLoading(true);
    api<Listing>(`/projects/${projectId}/pu-storage/files?folder_id=${encodeURIComponent(folder)}`)
      .then((res) => { if (alive) { setListing(res); setConnected(true); setError(""); } })
      .catch((err: unknown) => {
        if (!alive) return;
        setConnected(false);
        setListing(null);
        setError("");
        void err;
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [projectId, folder, tick]);

  async function connect() {
    setError("");
    try {
      await api(`/projects/${projectId}/pu-storage/connect`, { method: "POST" });
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подключить хранилище");
    }
  }

  async function createFolder() {
    const name = (newFolder ?? "").trim();
    if (!name) { setNewFolder(null); return; }
    try {
      await api(`/projects/${projectId}/pu-storage/folders`, { method: "POST", body: JSON.stringify({ parent_id: folder, name }) });
      setNewFolder(null);
      setTick((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать папку");
    }
  }

  const upload = useCallback(async (files: File[]) => {
    if (!files.length) return;
    const errors: string[] = [];
    setUploadErrors([]);
    for (let i = 0; i < files.length; i += 1) {
      setProgress(`Загружено ${i} из ${files.length}`);
      const file = files[i];
      try {
        const response = await fetch(
          `/projects/${projectId}/pu-storage/upload?parent_id=${encodeURIComponent(folder)}&name=${encodeURIComponent(file.name)}`,
          { method: "POST", body: file, credentials: "same-origin", headers: csrfHeaders() },
        );
        if (!response.ok) {
          errors.push(`${file.name}: ${response.status === 413 ? "не хватает места или файл слишком большой" : `ошибка ${response.status}`}`);
        }
      } catch {
        errors.push(`${file.name}: не удалось загрузить`);
      }
    }
    setProgress(files.length > 0 ? `Загружено ${files.length - errors.length} из ${files.length}` : "");
    setUploadErrors(errors);
    setTick((v) => v + 1);
  }, [projectId, folder]);

  async function download(row: FileRow) {
    try {
      const response = await fetch(`/projects/${projectId}/pu-storage/download?object_id=${encodeURIComponent(row.id)}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = row.is_folder ? `${row.name}.zip` : row.name;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать файл");
    }
  }

  if (!connected && !loading) {
    return (
      <div className="pus">
        <div className="pus-card">
          <h3 className="pus-title">Хранилище PU Workspace</h3>
          <p>Файлы проекта хранятся на сервере PU Workspace — свой Google Drive или Яндекс Диск не нужен.</p>
          {error && <p className="pus-error" role="alert">{error}</p>}
          <button type="button" onClick={() => void connect()}>Подключить хранилище</button>
        </div>
      </div>
    );
  }

  const usage = listing?.usage;
  const percent = usage && usage.quota_bytes > 0 ? Math.min(100, Math.round((usage.used_bytes / usage.quota_bytes) * 100)) : 0;
  const files = listing?.files ?? [];

  return (
    <div
      className="pus"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); void upload(Array.from(e.dataTransfer.files)); }}
    >
      <div className="pus-head">
        <h3 className="pus-title">Файлы проекта</h3>
        <div className="pus-actions">
          <button type="button" onClick={() => input.current?.click()}>Загрузить файлы</button>
          <button type="button" onClick={() => setNewFolder("")}>Новая папка</button>
          <input ref={input} hidden multiple type="file" aria-label="Выбрать файлы" onChange={(e) => { void upload(Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }} />
        </div>
      </div>
      <LocalFolderSync projectId={projectId} folder={folder} onDone={() => setTick((v) => v + 1)} />
      <nav className="pus-crumbs" aria-label="Путь">
        {crumbs(folder).map((c, i, all) => (
          <span key={c.id}>
            {i < all.length - 1 ? <button type="button" className="pus-link" onClick={() => setFolder(c.id)}>{c.name}</button> : <b>{c.name}</b>}
            {i < all.length - 1 && " / "}
          </span>
        ))}
      </nav>
      {usage && (
        <div className="pus-quota">
          <span>Занято {formatSize(usage.used_bytes)} из {formatSize(usage.quota_bytes)}</span>
          <div className="pus-bar"><div className="pus-bar-fill" style={{ width: `${percent}%` }} /></div>
        </div>
      )}
      {progress && <p className="pus-note" role="status">{progress}</p>}
      {uploadErrors.length > 0 && <ul className="pus-error" role="alert">{uploadErrors.map((e) => <li key={e}>{e}</li>)}</ul>}
      {error && <p className="pus-error" role="alert">{error}</p>}
      {newFolder !== null && (
        <div className="pus-newfolder">
          <input aria-label="Название папки" autoFocus value={newFolder} onChange={(e) => setNewFolder(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void createFolder(); }} />
          <button type="button" onClick={() => void createFolder()}>Создать</button>
          <button type="button" onClick={() => setNewFolder(null)}>Отмена</button>
        </div>
      )}
      {loading && <p className="pus-note">Загружаем…</p>}
      {!loading && files.length === 0 && <p className="pus-empty">Папка пуста. Загрузите файлы или перетащите их сюда.</p>}
      {files.length > 0 && (
        <div className="pus-wrap">
          <table className="pus-table">
            <thead><tr><th>Имя</th><th>Размер</th><th>Изменён</th><th></th></tr></thead>
            <tbody>
              {files.map((row) => (
                <tr key={row.id}>
                  <td>{row.is_folder ? <button type="button" className="pus-link" onClick={() => setFolder(row.id)}>📁 {row.name}</button> : row.name}</td>
                  <td>{row.is_folder ? "—" : formatSize(row.size)}</td>
                  <td>{formatDate(row.modified_time)}</td>
                  <td><button type="button" onClick={() => void download(row)}>{row.is_folder ? "Скачать zip" : "Скачать"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
