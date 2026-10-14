import { lazy as reactLazy, Suspense } from "react";
import type { ComponentProps, ComponentType } from "react";

const RELOAD_KEY = "pu-chunk-reload";
const RELOAD_COOLDOWN_MS = 30_000;

function isChunkLoadError(error: unknown): boolean {
  if (error == null) return false;
  const name = (error as { name?: unknown }).name;
  if (name === "ChunkLoadError") return true;
  const message = (error as { message?: unknown }).message;
  if (typeof message !== "string") return false;
  return (
    message.includes("Failed to fetch dynamically imported module") ||
    message.includes("Importing a module script failed") ||
    message.includes("error loading dynamically imported module")
  );
}

function tryReloadOnce(): boolean {
  try {
    if (typeof window === "undefined") return false;
    const raw = window.sessionStorage.getItem(RELOAD_KEY);
    const now = Date.now();
    if (raw != null) {
      const prev = Number(raw);
      if (!Number.isNaN(prev) && now - prev < RELOAD_COOLDOWN_MS) {
        return false;
      }
    }
    window.sessionStorage.setItem(RELOAD_KEY, String(now));
    window.location.reload();
    return true;
  } catch {
    return false;
  }
}

export function handleChunkLoadError(error: unknown): void {
  if (!isChunkLoadError(error)) throw error;
  if (tryReloadOnce()) {
    // Reload initiated — swallow so React error boundary isn't triggered.
    return;
  }
  throw error;
}

if (typeof window !== "undefined") {
  window.addEventListener("vite:preloadError", (event: Event) => {
    const detail = (event as Event & { payload?: unknown }).payload;
    if (!isChunkLoadError(detail)) return;
    tryReloadOnce();
  });
}

/** A lazily loaded screen with its own loading placeholder, so the rest of the shell renders at once. */
export function lazy<T extends ComponentType<any>>(loader: () => Promise<{ default: T }>): T {
  const wrappedLoader = () =>
    loader().catch((error: unknown) => {
      handleChunkLoadError(error);
      return new Promise<{ default: T }>(() => {});
    });
  const Component = reactLazy(wrappedLoader);
  function RouteChunk(props: ComponentProps<T>) {
    return (
      <Suspense fallback={<div className="app-route-loading" role="status">Загружаю экран…</div>}>
        <Component {...props} />
      </Suspense>
    );
  }
  return RouteChunk as T;
}
