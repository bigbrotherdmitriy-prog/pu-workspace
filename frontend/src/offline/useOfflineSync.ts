import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

// A lifetime token identifies one rendered scope instance. It is created fresh
// for every (userId, projectId) activation and invalidated on unmount or scope
// change. Handlers capture the token of the lifetime they were created in, so a
// retained handler from an earlier lifetime (even for the same user/project,
// e.g. A -> B -> A) can never become valid again.
type Lifetime = { scope: Scope; active: boolean };

// Per-effect setup epoch. Each effect setup bumps the epoch; cleanup invalidates
// it. Operations capture the epoch at START and re-check it after every await.
type Epoch = { value: number };

export function useOfflineSync(userId: number, projectId: number, onApplied: () => void) {
  const storageAvailable = typeof indexedDB !== "undefined";
  const [summary, setSummary] = useState<SyncSummary>(EMPTY);
  const [conflicts, setConflicts] = useState<ServerConflict[]>([]);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => { onAppliedRef.current = onApplied; }, [onApplied]);

  // Memoized per-scope lifetime identity. A new lifetime object is created only
  // when the scope (userId/projectId) actually changes, not on ordinary
  // same-scope re-renders. No shared history cache is kept across scopes.
  const lifetime = useMemo<Lifetime>(
    () => ({ scope: { userId, projectId }, active: true }),
    [userId, projectId],
  );

  // The currently active lifetime. Replaced on scope change and invalidated on
  // unmount. Handlers compare against this by identity, not by scope values.
  const lifetimeRef = useRef<Lifetime>(lifetime);
  // Latest refresh generation per scope; a newer refresh supersedes older ones.
  const refreshGenRef = useRef(0);
  // Current setup epoch; bumped on every effect setup, invalidated on cleanup.
  const epochRef = useRef<Epoch>({ value: 0 });

  const isLive = useCallback((token: Lifetime): boolean => {
    return token.active && lifetimeRef.current === token;
  }, []);

  // Invalidate outstanding async work and reset visible state on scope change.
  useEffect(() => {
    lifetime.active = true;
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
  }, [lifetime]);

  const refresh = useCallback(async () => {
    const token = lifetime;
    const scope: Scope = { userId, projectId };
    const epoch = epochRef.current;
    const refreshGen = ++refreshGenRef.current;
    const stillCurrent = () =>
      isLive(token) && sameScope(token.scope, scope) &&
      epochRef.current === epoch &&
      refreshGenRef.current === refreshGen;

    if (!stillCurrent()) return;
    if (!storageAvailable || !userId || !projectId) {
      if (stillCurrent()) {
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
  }, [userId, projectId, storageAvailable, isLive, lifetime]);

  const syncNow = useCallback(async (manual = false) => {
    const token = lifetime;
    const scope: Scope = { userId, projectId };
    const epoch = epochRef.current;
    const stillCurrent = () =>
      isLive(token) && sameScope(token.scope, scope) && epochRef.current === epoch;

    if (!stillCurrent()) return;
    if (!storageAvailable || !userId || !projectId) return;
    let before: number;
    try {
      before = (await offlineSummary(userId, projectId)).total;
    } catch {
      return;
    }
    if (!stillCurrent()) return;
    // Live-guard callback: the engine invokes this before/after journal awaits.
    // It throws when the captured scope/lifetime/epoch is no longer current so
    // the engine aborts stale async work instead of continuing.
    const assertCurrentScope = () => {
      if (!stillCurrent()) throw new Error("scope changed");
    };
    try {
      await synchronize(userId, projectId, manual, assertCurrentScope);
    } catch (reason) {
      // Quietly abort only when the scope went stale; otherwise surface the
      // real error to the caller.
      if (!stillCurrent()) return;
      throw reason;
    }
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
  }, [userId, projectId, refresh, storageAvailable, isLive, lifetime]);

  useEffect(() => {
    if (!storageAvailable || !userId || !projectId) return;
    const epoch: Epoch = { value: epochRef.current.value + 1 };
    epochRef.current = epoch;
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
      if (epochRef.current === epoch) {
        epochRef.current = { value: epoch.value + 1 };
      }
    };
  }, [userId, projectId, refresh, syncNow, storageAvailable]);

  async function resolve(id: number, resolution: "keep_server" | "apply_local" | "latest_write_wins") {
    const token = lifetime;
    const scope: Scope = { userId, projectId };
    const epoch = epochRef.current;
    const stillCurrent = () =>
      isLive(token) && sameScope(token.scope, scope) && epochRef.current === epoch;

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
