import { lazy as reactLazy, Suspense } from "react";
import type { ComponentProps, ComponentType } from "react";

/** A lazily loaded screen with its own loading placeholder, so the rest of the shell renders at once. */
export function lazy<T extends ComponentType<any>>(loader: () => Promise<{ default: T }>): T {
  const Component = reactLazy(loader);
  function RouteChunk(props: ComponentProps<T>) {
    return (
      <Suspense fallback={<div className="app-route-loading" role="status">Загружаю экран…</div>}>
        <Component {...props} />
      </Suspense>
    );
  }
  return RouteChunk as T;
}
