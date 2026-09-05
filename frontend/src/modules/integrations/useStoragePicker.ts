import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { api, ApiError } from "../../api/client";

export type PickerContext = {
  project_id: number;
  provider: string;
  connection_id: string | null;
  connection_row_id: number | null;
};
export type PickerFolder = {
  id: string;
  name: string;
  provider?: string;
  registered: boolean;
  snapshot_id?: number;
  snapshot_status?: string;
};
type Discovery<T> = PickerContext & {
  folder_id: string;
  breadcrumbs: { id: string; name: string }[];
  folders: T[];
};
export type StorageSelection = PickerContext & {
  folder_id: string;
  source_folder: string;
  id: number;
  job_id: number | null;
  status: string;
  analysis_status?: string;
  analysis_error?: string | null;
};
const canonical = (provider: string) => provider === "google_workspace" ? "google_drive" : provider;
const storageKey = (projectId: number) => `pu_storage_selection_v1:${projectId}`;
const reopenMessage = "Подключение или проект изменились. Переоткройте выбор папки. Если нужен другой диск, сначала выберите его подключение в настройках проекта.";

export function restoredStorageSelection(projectId: number): StorageSelection | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(projectId)) || "null");
    return value?.project_id === projectId && typeof value.id === "number" && typeof value.folder_id === "string" ? value : null;
  } catch { return null; }
}

function matches(expected: PickerContext, actual: PickerContext, allowNewRow = false) {
  return expected.project_id === actual.project_id && canonical(expected.provider) === canonical(actual.provider)
    && expected.connection_id === actual.connection_id
    && (expected.connection_row_id === actual.connection_row_id || (allowNewRow && expected.connection_row_id === null && actual.connection_row_id !== null));
}

function guards(context: Pick<PickerContext, "provider" | "connection_id">) {
  const query = new URLSearchParams({ provider: context.provider });
  if (context.connection_id !== null) query.set("connection_id", context.connection_id);
  return query;
}

export function useStoragePicker<T extends PickerFolder>(projectId: number, projectIdRef: MutableRefObject<number>) {
  const [isOpen, setOpen] = useState(false);
  const [context, setContext] = useState<PickerContext | null>(null);
  const [folders, setFolders] = useState<T[]>([]);
  const [folderId, setFolderId] = useState("");
  const [breadcrumbs, setBreadcrumbs] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyFolder, setBusyFolder] = useState("");
  const [error, setError] = useState("");
  const [needsReopen, setNeedsReopen] = useState(false);
  const [selection, setSelection] = useState<StorageSelection | null>(() => restoredStorageSelection(projectId));
  const active = useRef<PickerContext | null>(null);
  const epoch = useRef(0);
  const discoverySequence = useRef(0);
  const confirming = useRef(false);
  const desiredProvider = useRef<string | undefined>(undefined);
  const submissionSequence = useRef(0);
  const latestSubmission = useRef(new Map<number, number>());

  function close() {
    epoch.current += 1;
    discoverySequence.current += 1;
    confirming.current = false;
    active.current = null;
    setOpen(false); setContext(null); setFolders([]); setBreadcrumbs([]);
    setLoading(false); setBusyFolder(""); setError(""); setNeedsReopen(false);
  }
  useEffect(() => {
    close();
    setSelection(restoredStorageSelection(projectId));
  }, [projectId]);
  useEffect(() => () => { epoch.current += 1; }, []);

  function capture() {
    const pinned = active.current;
    const generation = epoch.current;
    const sequence = discoverySequence.current;
    if (!pinned || pinned.project_id !== projectIdRef.current) return null;
    return { context: pinned, current: () => epoch.current === generation && discoverySequence.current === sequence
      && projectIdRef.current === pinned.project_id && active.current === pinned };
  }

  function fail(cause: unknown) {
    const conflict = cause instanceof ApiError && cause.status === 409;
    setError(conflict ? reopenMessage : cause instanceof Error ? cause.message : "Не удалось загрузить папки");
    // A failed request must never leave the previous folder list confirmable.
    active.current = null; setContext(null); setFolders([]); setNeedsReopen(true);
  }

  async function discover(target: number, provider: string | undefined, path?: string, pinned?: PickerContext) {
    const generation = epoch.current;
    const sequence = ++discoverySequence.current;
    const current = () => generation === epoch.current && sequence === discoverySequence.current && target === projectIdRef.current;
    setLoading(true); setError(""); setNeedsReopen(false);
    try {
      const query = pinned ? guards(pinned) : new URLSearchParams(provider ? { provider } : {});
      if (path !== undefined) query.set("folder_id", path);
      const suffix = query.size ? `?${query}` : "";
      const result = await api<Discovery<T>>(`/projects/${target}/source-folders/discover${suffix}`);
      if (!current()) return;
      if (result.project_id !== target || (provider && canonical(provider) !== canonical(result.provider))
          || (pinned && !matches(pinned, result))) throw new ApiError(reopenMessage, 409, "");
      const next = { project_id: result.project_id, provider: result.provider,
        connection_id: result.connection_id, connection_row_id: result.connection_row_id };
      active.current = next; setContext(next);
      setFolders(result.folders); setFolderId(result.folder_id); setBreadcrumbs(result.breadcrumbs);
      const saved = restoredStorageSelection(target);
      if (saved) {
        const snapshots = await api<{ snapshots: { id: number; project_id: number; status: string; analysis_status?: string; analysis_error?: string | null }[] }>(`/projects/${target}/snapshots`);
        if (!current()) return;
        const snapshot = snapshots.snapshots.find(item => item.id === saved.id && item.project_id === target);
        if (snapshot) setSelection({ ...saved, status: snapshot.status, analysis_status: snapshot.analysis_status, analysis_error: snapshot.analysis_error });
      }
    } catch (cause) {
      if (current()) fail(cause);
    } finally { if (current()) setLoading(false); }
  }

  async function open(provider?: string) {
    close();
    desiredProvider.current = provider;
    const target = projectIdRef.current;
    setOpen(true);
    if (!target) { setError("Сначала выберите проект"); return; }
    // No root-setting PUT: omitted folder_id restores the backend's selected folder.
    await discover(target, provider);
  }

  async function navigate(path?: string) {
    if (confirming.current) return;
    const pinned = active.current;
    if (!pinned || pinned.project_id !== projectIdRef.current) return;
    await discover(pinned.project_id, pinned.provider, path, pinned);
  }

  async function confirm(folder: T) {
    const request = capture();
    if (!request || loading || confirming.current || needsReopen) return false;
    const pinned = request.context;
    const generation = epoch.current;
    if (folder.provider && canonical(folder.provider) !== canonical(pinned.provider)) {
      fail(new ApiError(reopenMessage, 409, "")); return false;
    }
    const submission = ++submissionSequence.current;
    latestSubmission.current.set(pinned.project_id, submission);
    confirming.current = true; setBusyFolder(folder.id); setError("");
    try {
      const result = await api<StorageSelection>(`/projects/${pinned.project_id}/source-folders/${encodeURIComponent(folder.id)}/snapshot-queue?${guards(pinned)}`, { method: "POST" });
      if (!matches(pinned, result, true) || result.folder_id !== folder.id) throw new ApiError(reopenMessage, 409, "");
      // Retain a verified result under its own project even if the user left it.
      if (latestSubmission.current.get(pinned.project_id) === submission) {
        try { sessionStorage.setItem(storageKey(pinned.project_id), JSON.stringify(result)); } catch { /* Storage may be disabled. */ }
      }
      if (!request.current()) return false;
      const next = { ...pinned, connection_row_id: result.connection_row_id };
      active.current = next; setContext(next); setSelection(result);
      setFolders(items => items.map(item => item.id === folder.id
        ? { ...item, registered: true, snapshot_id: result.id, snapshot_status: result.status } : item));
      return true;
    } catch (cause) {
      if (request.current()) fail(cause);
      return false;
    } finally {
      if (epoch.current === generation && projectIdRef.current === pinned.project_id) {
        confirming.current = false; setBusyFolder("");
      }
    }
  }

  return { isOpen, context, folders, setFolders, folderId, breadcrumbs, loading, error, needsReopen,
    busyFolder, setBusyFolder, selection, open, navigate, confirm, close, capture,
    reopen: () => open(desiredProvider.current) };
}
