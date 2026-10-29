import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";

const CustomerPortal = lazy(() => import("./CustomerPortal").then((m) => ({ default: m.CustomerPortal })));
type Project = { id: number; name: string };

/** Пользователь только с доступом заказчика видит кабинет заказчика, остальные — обычное приложение. */
export function CustomerGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "app" | "customer">("checking");
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const customer = await api<{ projects: Project[] }>("/customer/projects");
        if (!customer?.projects?.length) { if (alive) setState("app"); return; }
        const own = await api<{ projects: Project[] } | Project[]>("/projects/");
        const ownList = Array.isArray(own) ? own : own?.projects ?? [];
        if (!alive) return;
        if (ownList.length === 0) { setProjects(customer.projects); setState("customer"); } else setState("app");
      } catch {
        if (alive) setState("app"); // не вошли в систему или сеть: обычное приложение покажет вход
      }
    })();
    return () => { alive = false; };
  }, []);

  if (state === "checking") return <div className="app-route-loading" role="status">Загружаю…</div>;
  if (state === "customer") return <Suspense fallback={<div className="app-route-loading" role="status">Загружаю…</div>}><CustomerPortal projects={projects} /></Suspense>;
  return <>{children}</>;
}
