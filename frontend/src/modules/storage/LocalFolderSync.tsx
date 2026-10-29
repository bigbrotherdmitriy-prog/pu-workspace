import { useRef, useState } from "react";
import { csrfHeaders } from "../../api/client";
import { api } from "../../api/client";

// File System Access API (Chrome, Edge, Яндекс Браузер): типы объявляем минимально.
type FileHandleLike = { kind: "file"; name: string; getFile: () => Promise<File> };
type DirHandleLike = {
  kind: "directory";
  name: string;
  entries: () => AsyncIterable<[string, FileHandleLike | DirHandleLike]>;
};

const MAX_FILE_BYTES = 100 * 1024 * 1024;
const storeKey = (projectId: number, folder: string) => `pu-local-sync:${projectId}:${folder}`;

type Known = Record<string, string>;

function readKnown(key: string): Known {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" ? (value as Known) : {};
  } catch {
    return {};
  }
}

function writeKnown(key: string, known: Known) {
  try { localStorage.setItem(key, JSON.stringify(known)); } catch { /* the sync still works without memory */ }
}

async function collect(dir: DirHandleLike, prefix = ""): Promise<{ path: string; handle: FileHandleLike }[]> {
  const result: { path: string; handle: FileHandleLike }[] = [];
  for await (const [name, handle] of dir.entries()) {
    if (name.startsWith(".")) continue;
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === "directory") result.push(...(await collect(handle as DirHandleLike, path)));
    else result.push({ path, handle: handle as FileHandleLike });
  }
  return result;
}

export function LocalFolderSync({ projectId, folder, onDone }: { projectId: number; folder: string; onDone: () => void }) {
  const supported = typeof window !== "undefined" && "showDirectoryPicker" in window;
  const [status, setStatus] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const cancel = useRef(false);

  async function ensureFolder(parentId: string, name: string, cache: Map<string, string>): Promise<string> {
    const key = `${parentId}/${name}`;
    const cached = cache.get(key);
    if (cached) return cached;
    const res = await api<{ id: string }>(`/projects/${projectId}/pu-storage/folders`, {
      method: "POST",
      body: JSON.stringify({ parent_id: parentId, name }),
    });
    cache.set(key, res.id);
    return res.id;
  }

  async function sync() {
    setErrors([]);
    setStatus("");
    let dir: DirHandleLike;
    try {
      dir = await (window as unknown as { showDirectoryPicker: () => Promise<DirHandleLike> }).showDirectoryPicker();
    } catch {
      return; // the user closed the picker
    }
    cancel.current = false;
    setBusy(true);
    const key = storeKey(projectId, `${folder}|${dir.name}`);
    const known = readKnown(key);
    const failed: string[] = [];
    let uploaded = 0;
    let skipped = 0;
    try {
      setStatus("Читаем папку…");
      const files = await collect(dir);
      const root = await ensureFolder(folder, dir.name, new Map());
      const folderCache = new Map<string, string>();
      for (let i = 0; i < files.length; i += 1) {
        if (cancel.current) break;
        const { path, handle } = files[i];
        setStatus(`Передано ${uploaded}, без изменений ${skipped}, всего ${files.length}`);
        try {
          const file = await handle.getFile();
          const signature = `${file.size}:${file.lastModified}`;
          if (known[path] === signature) { skipped += 1; continue; }
          if (file.size > MAX_FILE_BYTES) { failed.push(`${path}: больше 100 МБ`); continue; }
          let parent = root;
          const parts = path.split("/");
          for (const part of parts.slice(0, -1)) parent = await ensureFolder(parent, part, folderCache);
          const response = await fetch(
            `/projects/${projectId}/pu-storage/upload?parent_id=${encodeURIComponent(parent)}&name=${encodeURIComponent(file.name)}`,
            { method: "POST", body: file, credentials: "same-origin", headers: csrfHeaders() },
          );
          if (!response.ok) {
            failed.push(`${path}: ${response.status === 413 ? "не хватает места" : `ошибка ${response.status}`}`);
            if (response.status === 413) break;
            continue;
          }
          known[path] = signature;
          uploaded += 1;
        } catch (err) {
          failed.push(`${path}: ${err instanceof Error ? err.message : "не удалось передать"}`);
        }
      }
      writeKnown(key, known);
      setStatus(`${cancel.current ? "Остановлено. " : ""}Передано: ${uploaded}, без изменений: ${skipped}${failed.length ? `, с ошибками: ${failed.length}` : ""}.`);
      setErrors(failed);
      onDone();
    } catch (err) {
      setStatus("");
      setErrors([err instanceof Error ? err.message : "Не удалось прочитать папку"]);
    } finally {
      setBusy(false);
    }
  }

  if (!supported) {
    return <p className="pus-note">Синхронизация папки с компьютера работает в Chrome, Edge и Яндекс Браузере.</p>;
  }
  return (
    <div className="pus-sync">
      <button type="button" disabled={busy} onClick={() => void sync()}>Синхронизировать папку с компьютера</button>
      {busy && <button type="button" onClick={() => { cancel.current = true; }}>Остановить</button>}
      {status && <p className="pus-note" role="status">{status}</p>}
      {errors.length > 0 && <ul className="pus-error" role="alert">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
      <p className="pus-note">Передаются новые и изменённые файлы. Вкладку нужно держать открытой, пока идёт передача.</p>
    </div>
  );
}
