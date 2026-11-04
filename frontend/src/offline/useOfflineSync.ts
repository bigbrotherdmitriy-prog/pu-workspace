import { useCallback, useEffect, useRef, useState } from "react";
import {
  CHANGE_EVENT, offlineSummary, recoverAuthRequired, resolveConflict, serverConflicts, synchronize,
} from "./syncEngine";
import { CONNECTIVITY_EVENT, isOnline } from "./connectivity";
import type { ServerConflict, SyncSummary } from "./types";
import { resumeOfflineSession } from "./lifecycle";

const EMPTY: SyncSummary = {
  queued: 0, syncing: 0, attention: 0, authRequired: 0, conflicts: 0, total: 0,
};

export function useOfflineSync(userId: number, projectId: number, onApplied: () => void) {
  const storageAvailable = typeof indexedDB !== "undefined";
  const [summary, setSummary] = useState<SyncSummary>(EMPTY);
  const [conflicts, setConflicts] = useState<ServerConflict[]>([]);
  const [recoveringAuth, setRecoveringAuth] = useState(false);
  const recoveryRunning = useRef(false);
  // Identity changes even for A -> B -> A; string equality cannot fence late work.
  const key = `${userId}:${projectId}`;
  const currentScope = useRef({ key, active: false, generation: 0 });
  if (currentScope.current.key !== key) currentScope.current = { key, active: false, generation: 0 };
  const scope = currentScope.current;
  const isCurrent = (generation: number) => currentScope.current === scope && scope.active && scope.generation === generation;
  useEffect(() => {
    if (userId > 0 && projectId > 0) resumeOfflineSession(userId);
    scope.active = true;
    scope.generation += 1;
    recoveryRunning.current = false;
    setRecoveringAuth(false);
    setSummary(EMPTY);
    setConflicts([]);
    return () => { scope.active = false; scope.generation += 1; };
  }, [scope]);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  const refresh = useCallback(async () => {
    const generation = scope.generation;
    if (!isCurrent(generation)) return;
    if (!storageAvailable) {
      setSummary(EMPTY);
      setConflicts([]);
      return;
    }
    const nextSummary = await offlineSummary(userId, projectId);
    if (!isCurrent(generation)) return;
    setSummary(nextSummary);
    if (isOnline() && userId && projectId) {
      try {
        const nextConflicts = await serverConflicts(projectId);
        if (isCurrent(generation)) setConflicts(nextConflicts);
      } catch { /* Preserve local state. */ }
    }
  }, [userId, projectId, storageAvailable, scope]);

  const syncNow = useCallback(async (manual = false) => {
    const generation = scope.generation;
    if (!storageAvailable || !isCurrent(generation)) return;
    const before = (await offlineSummary(userId, projectId)).total;
    if (!isCurrent(generation)) return;
    await synchronize(userId, projectId, manual);
    if (!isCurrent(generation)) return;
    const after = (await offlineSummary(userId, projectId)).total;
    if (!isCurrent(generation)) return;
    await refresh();
    if (isCurrent(generation) && after < before) onAppliedRef.current();
  }, [userId, projectId, refresh, storageAvailable, scope]);

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
    const generation = scope.generation;
    if (!storageAvailable || !isCurrent(generation)) return;
    await resolveConflict(userId, projectId, id, resolution);
    if (!isCurrent(generation)) return;
    await refresh();
    if (isCurrent(generation)) onAppliedRef.current();
  }

  async function recoverAuth() {
    const generation = scope.generation;
    if (!isCurrent(generation)) return;
    if (recoveryRunning.current) return;
    if (!storageAvailable) throw new Error("Локальная очередь недоступна в этом браузере.");
    recoveryRunning.current = true;
    setRecoveringAuth(true);
    try {
      await recoverAuthRequired(userId, projectId);
      if (isCurrent(generation)) await syncNow(true);
    } finally {
      if (isCurrent(generation)) {
        recoveryRunning.current = false;
        setRecoveringAuth(false);
      }
    }
  }

  return { summary, conflicts, syncNow, resolve, recoverAuth, recoveringAuth };
}
