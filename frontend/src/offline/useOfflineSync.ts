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

// A lifetime token identifies one mounted scope instance. It is created fresh
// for every (userId, projectId) activation and invalidated on unmount or scope
// change. Handlers capture the token of the lifetime they were created in, so a
// retained handler from an earlier lifetime (even for the same user/project,
// e.g. A -> B -> A) can never become valid again.
type Lifetime = { scope: Scope; active: boolean };

export function useOfflineSync(userId: number, projectId: number, onApplied: () => void) {
  const storageAvailable = typeof indexedDB !== "undefined";
  const [summary, setSummary] = useState<SyncSummary>(EMPTY);
  const [conflicts, setConflicts] = useState<ServerConflict[]>([]);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  // The currently active lifetime. Replaced on scope change and invalidated on
  // unmount. Handlers compare against this by identity, not by scope values.
  const lifetimeRef = useRef<Lifetime>({ scope: { userId, projectId }, active: true });
  // Latest refresh generation per scope; a newer refresh supersedes older ones.
  const refreshGenRef = useRef(0);

  const isLive = useCallback((lifetime: Lifetime): boolean => {
    return lifetime.active && lifetimeRef.current === lifetime;
  }, []);

  // Invalidate outstanding async work and reset visible state on scope change.
  useEffect(() => {
    const lifetime: Lifetime = { scope: { userId, projectId }, active: true };
    lifetimeRef.current = lifetime;
    refreshGenRef.current += 1;
    setSummary(EMPTY);
    setConflicts([]);
    return () => {
      lifetime.active = false;
      if (lifetimeRef.current === lifetime) {
        lifetimeRef.current = { scope: lifetime.scope, active: false };
      }
      refreshGenRef.current += 1;
    };
  }, [userId, projectId]);

  const refresh = useCallback(async () => {
    const lifetime = lifetimeRef.current;
    const scope: Scope = { userId, projectId };
    const refreshGen = ++refreshGenRef.current;
    const stillCurrent = () =>
      isLive(lifetime) && sameScope(lifetime.scope, scope) &&
      refreshGenRef.current === refreshGen;

    if (!isLive(lifetime)) return;
    if (!storageAvailable || !userId || !projectId) {
      if (isLive(lifetime)) {
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
  }, [userId, projectId, storageAvailable, isLive]);

  const syncNow = useCallback(async (manual = false) => {
    const lifetime = lifetimeRef.current;
    const scope: Scope = { userId, projectId };
    const stillCurrent = () => isLive(lifetime) && sameScope(lifetime.scope, scope);

    if (!stillCurrent()) return;
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
  }, [userId, projectId, refresh, storageAvailable, isLive]);

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
    const lifetime = lifetimeRef.current;
    const scope: Scope = { userId, projectId };
    const stillCurrent = () => isLive(lifetime) && sameScope(lifetime.scope, scope);

    if (!stillCurrent()) return;
    if (!storageAvailable || !userId || !projectId) return;
    await resolveConflict(userId, projectId, id, resolution);
    if (!stillCurrent()) return;
    await refresh();
    if (!stillCurrent()) return;
    onAppliedRef.current();
  }

  return { summary, conflicts, syncNow, resolve };
}
