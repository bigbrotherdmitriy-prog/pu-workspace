// Robust online/offline detection.
//
// `navigator.onLine` and the window `online`/`offline` events are unreliable on
// Android WebView/Chrome when a VPN is active: the browser can report "offline"
// while the server is perfectly reachable. We therefore treat the app as offline
// only when `navigator.onLine === false` AND a real reachability probe against a
// cheap same-origin endpoint fails. Any successful API response clears the
// offline state immediately. While offline we re-probe periodically; while
// online we never probe.

export const HEALTH_PATH = "/health";
export const PROBE_TIMEOUT_MS = 5_000;
export const OFFLINE_RECHECK_MS = 30_000;
export const CONNECTIVITY_EVENT = "pu-connectivity-changed";

type Listener = (online: boolean) => void;

// `true` once the server was confirmed reachable (probe or API response) while
// the browser may claim to be offline; `false` after a failed probe.
let reachable: boolean | null = null;
let lastReported: boolean | null = null;
let inflight: Promise<boolean> | null = null;
let recheckTimer: ReturnType<typeof setInterval> | null = null;
let monitors = 0;
let detachMonitor: (() => void) | null = null;
const listeners = new Set<Listener>();

function browserSaysOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

export function isOnline(): boolean {
  return browserSaysOnline() || reachable === true;
}

function syncRecheckTimer() {
  if (monitors > 0 && !isOnline()) {
    if (recheckTimer === null) recheckTimer = setInterval(() => { void recheckConnectivity(); }, OFFLINE_RECHECK_MS);
  } else if (recheckTimer !== null) {
    clearInterval(recheckTimer);
    recheckTimer = null;
  }
}

function emit() {
  const online = isOnline();
  syncRecheckTimer();
  if (online === lastReported) return;
  lastReported = online;
  listeners.forEach((listener) => listener(online));
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(CONNECTIVITY_EVENT, { detail: { online } }));
}

export function subscribeConnectivity(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Called by the API client whenever the server answered (any HTTP status). */
export function reportServerReachable() {
  if (reachable === true) return;
  reachable = true;
  emit();
}

export async function probeReachability(timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = setTimeout(() => controller?.abort(), timeoutMs);
  try {
    const response = await fetch(`${HEALTH_PATH}?t=${Date.now()}`, {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
      signal: controller?.signal,
    });
    // Any HTTP answer proves the network path to the server works.
    return response.status > 0;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** Re-evaluate connectivity. Probes the server only when the browser claims offline. */
export function recheckConnectivity(): Promise<boolean> {
  if (browserSaysOnline()) {
    emit();
    return Promise.resolve(true);
  }
  if (inflight) return inflight;
  inflight = probeReachability()
    .then((ok) => {
      reachable = ok;
      return ok;
    })
    .finally(() => {
      inflight = null;
      emit();
    });
  return inflight;
}

/**
 * The browser just lost the network. A real offline must show at once, so the
 * earlier "server reachable" evidence is dropped and the banner appears before
 * the probe returns; the probe then confirms it, or clears it when the server
 * still answers (the Android VPN case).
 */
export function markOfflineAndRecheck(): Promise<boolean> {
  if (browserSaysOnline()) return recheckConnectivity();
  reachable = null;
  emit();
  return recheckConnectivity();
}

/** Attach window/document listeners (ref-counted). Returns a disposer. */
export function startConnectivityMonitor(): () => void {
  monitors += 1;
  if (monitors === 1 && typeof window !== "undefined") {
    const onOnline = () => { void recheckConnectivity(); };
    const onOffline = () => { void markOfflineAndRecheck(); };
    const onVisibility = () => { if (document.visibilityState === "visible") void recheckConnectivity(); };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisibility);
    detachMonitor = () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    void recheckConnectivity();
  }
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    monitors -= 1;
    if (monitors === 0) {
      detachMonitor?.();
      detachMonitor = null;
    }
    syncRecheckTimer();
  };
}

export function resetConnectivityForTests() {
  reachable = null;
  lastReported = null;
  inflight = null;
  if (recheckTimer !== null) clearInterval(recheckTimer);
  recheckTimer = null;
  detachMonitor?.();
  detachMonitor = null;
  monitors = 0;
  listeners.clear();
}
