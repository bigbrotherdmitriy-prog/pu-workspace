import { useCallback, useEffect, useRef, useState } from "react";
import {
  CHANGE_EVENT, offlineSummary, recoverAuthRequired, resolveConflict, serverConflicts, synchronize,
} from "./syncEngine";
import { CONNECTIVITY_EVENT, isOnline } from "./connectivity";
import type { ServerConflict, SyncSummary } from "./types";

const EMPTY: SyncSummary = {
  queued: 0, syncing: 0, attention: 0, authRequired: 0, conflicts: 0, total: 0,
};

export function useOfflineSync(userId: number, projectId: number, onApplied: () => void) {
  const storageAvailable = typeof indexedDB !== "undefined";
  const [summary, setSummary] = useState<SyncSummary>(EMPTY);
  const [conflicts, setConflicts] = useState<ServerConflict[]>([]);
  const [recoveringAuth, setRecoveringAuth] = useState(false);
  const recoveryRunning = useRef(false);
  const currentScope = useRef(`${userId}:${projectId}`);
  currentScope.current = `${userId}:${projectId}`;
  useEffect(() => {
    const scope = `${userId}:${projectId}`;
    currentScope.current = scope;
    return () => { if (currentScope.current === scope) currentScope.current = ""; };
  }, [userId, projectId]);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  const refresh = useCallback(async () => {
    if (!storageAvailable) {
      setSummary(EMPTY);
      setConflicts([]);
      return;
    }
    setSummary(await offlineSummary(userId, projectId));
    if (isOnline() && userId && projectId) {
      try { setConflicts(await serverConflicts(projectId)); } catch { /* Preserve local state. */ }
    }
  }, [userId, projectId, storageAvailable]);

  const syncNow = useCallback(async (manual = false) => {
    if (!storageAvailable) return;
    const before = (await offlineSummary(userId, projectId)).total;
    await synchronize(userId, projectId, manual);
    const after = (await offlineSummary(userId, projectId)).total;
    await refresh();
    if (after < before) onAppliedRef.current();
  }, [userId, projectId, refresh, storageAvailable]);

  useEffect(() => {
    if (!storageAvailable || !userId || !projectId) return;
    const changed = () => { void refresh(); };
    const online = () => { void syncNow(false); };
    const connectivity = () => { if (isOnline()) void syncNow(false); };
    const visible = () => { if (document.visibilityState === "visible") void syncNow(false); };
    window.addEventListener(CHANGE_EVENT, changed);
    window.addEventListener("online", online);
    window.addEventListener(CONNECTIVITY_EVENT, connectivity);
    document.addEventListener("visibilitychange", visible);
    void refresh();
    void syncNow(false);
    const timer = window.setInterval(() => { void syncNow(false); }, 30_000);
    return () => {
      window.removeEventListener(CHANGE_EVENT, changed);
      window.removeEventListener("online", online);
      window.removeEventListener(CONNECTIVITY_EVENT, connectivity);
      document.removeEventListener("visibilitychange", visible);
      window.clearInterval(timer);
    };
  }, [userId, projectId, refresh, syncNow, storageAvailable]);

  async function resolve(id: number, resolution: "keep_server" | "apply_local" | "latest_write_wins") {
    if (!storageAvailable) return;
    await resolveConflict(userId, projectId, id, resolution);
    await refresh();
    onAppliedRef.current();
  }

  async function recoverAuth() {
    if (recoveryRunning.current) return;
    if (!storageAvailable) throw new Error("Локальная очередь недоступна в этом браузере.");
    const scope = `${userId}:${projectId}`;
    recoveryRunning.current = true;
    setRecoveringAuth(true);
    try {
      await recoverAuthRequired(userId, projectId);
      if (currentScope.current === scope) await syncNow(true);
    } finally {
      recoveryRunning.current = false;
      setRecoveringAuth(false);
    }
  }

  return { summary, conflicts, syncNow, resolve, recoverAuth, recoveringAuth };
}
