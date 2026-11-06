import { useCallback, useEffect, useRef, useState } from "react";
import {
  CHANGE_EVENT, offlineSummary, resolveConflict, serverConflicts, synchronize,
} from "./syncEngine";
import { CONNECTIVITY_EVENT, isOnline } from "./connectivity";
import type { ServerConflict, SyncSummary } from "./types";

const EMPTY: SyncSummary = {
  queued: 0, syncing: 0, attention: 0, authRequired: 0, conflicts: 0, total: 0,
};

type Scope = { userId: number; projectId: number };

function sameScope(a: Scope, b: Scope): boolean {
  return a.userId === b.userId && a.projectId === b.projectId;
}

export function useOfflineSync(userId: number, projectId: number, onApplied: () => void) {
  const storageAvailable = typeof indexedDB !== "undefined";
  const [summary, setSummary] = useState<SyncSummary>(EMPTY);
  const [conflicts, setConflicts] = useState<ServerConflict[]>([]);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  // Lifetime generation: bumped on unmount and whenever the active scope changes.
  const generationRef = useRef(0);
  // Latest refresh generation per scope; a newer refresh supersedes older ones.
  const refreshGenRef = useRef(0);
  const scopeRef = useRef<Scope>({ userId, projectId });

  const isCurrent = useCallback((gen: number, scope: Scope): boolean => {
    return generationRef.current === gen && sameScope(scopeRef.current, scope);
  }, []);

  // Invalidate outstanding async work and reset visible state on scope change.
  useEffect(() => {
    generationRef.current += 1;
    refreshGenRef.current += 1;
    scopeRef.current = { userId, projectId };
    setSummary(EMPTY);
    setConflicts([]);
    return () => {
      generationRef.current += 1;
      refreshGenRef.current += 1;
    };
  }, [userId, projectId]);

  const refresh = useCallback(async () => {
    const gen = generationRef.current;
    const scope: Scope = { userId, projectId };
    const refreshGen = ++refreshGenRef.current;
    const stillCurrent = () =>
      isCurrent(gen, scope) && refreshGenRef.current === refreshGen;

    if (!storageAvailable || !userId || !projectId) {
      if (isCurrent(gen, scope)) {
        setSummary(EMPTY);
        setConflicts([]);
      }
      return;
    }
    let nextSummary: SyncSummary;
    try {
      nextSummary = await offlineSummary(userId, projectId);
    } catch {
      return;
    }
    if (!stillCurrent()) return;
    setSummary(nextSummary);
    if (isOnline()) {
      try {
        const nextConflicts = await serverConflicts(projectId);
        if (!stillCurrent()) return;
        setConflicts(nextConflicts);
      } catch { /* Preserve local state. */ }
    }
  }, [userId, projectId, storageAvailable, isCurrent]);

  const syncNow = useCallback(async (manual = false) => {
    const gen = generationRef.current;
    const scope: Scope = { userId, projectId };
    const stillCurrent = () => isCurrent(gen, scope);

    if (!storageAvailable || !userId || !projectId) return;
    let before: number;
    try {
      before = (await offlineSummary(userId, projectId)).total;
    } catch {
      return;
    }
    if (!stillCurrent()) return;
    await synchronize(userId, projectId, manual);
    if (!stillCurrent()) return;
    let after: number;
    try {
      after = (await offlineSummary(userId, projectId)).total;
    } catch {
      return;
    }
    if (!stillCurrent()) return;
    await refresh();
    if (!stillCurrent()) return;
    if (after < before) onAppliedRef.current();
  }, [userId, projectId, refresh, storageAvailable, isCurrent]);

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
    const gen = generationRef.current;
    const scope: Scope = { userId, projectId };
    const stillCurrent = () => isCurrent(gen, scope);

    if (!storageAvailable || !userId || !projectId) return;
    await resolveConflict(userId, projectId, id, resolution);
    if (!stillCurrent()) return;
    await refresh();
    if (!stillCurrent()) return;
    onAppliedRef.current();
  }

  return { summary, conflicts, syncNow, resolve };
}
