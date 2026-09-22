import { AlertTriangle, Check, RefreshCw, WifiOff } from "lucide-react";
import type { ServerConflict, SyncSummary } from "../../offline/types";

type Props = {
  online: boolean;
  summary: SyncSummary;
  conflicts: ServerConflict[];
  onSync: () => void;
  onResolve: (id: number, resolution: "keep_server" | "apply_local" | "latest_write_wins") => void;
};

function value(input: unknown) {
  if (input === null || input === undefined || input === "") return "—";
  return typeof input === "string" ? input : JSON.stringify(input);
}

export function SyncStatusPanel({ online, summary, conflicts, onSync, onResolve }: Props) {
  const needsAttention = summary.attention + summary.authRequired + conflicts.length;
  const label = !online
    ? `Офлайн · несинхронизировано: ${summary.total}`
    : summary.syncing
      ? `Синхронизация… осталось: ${summary.total}`
      : needsAttention
        ? `Требует внимания: ${needsAttention}`
        : summary.total
          ? `Ожидает синхронизации: ${summary.total}`
          : summary.lastSyncedAt
            ? `Синхронизировано · ${new Date(summary.lastSyncedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
            : "Синхронизация готова";

  return <section className={`mobile-sync-panel${needsAttention ? " has-attention" : ""}`} aria-live="polite">
    <div className="mobile-sync-summary">
      {!online ? <WifiOff /> : needsAttention ? <AlertTriangle /> : <Check />}
      <span>{label}</span>
      <button type="button" onClick={onSync} disabled={!online || Boolean(summary.syncing)}>
        <RefreshCw /> Синхронизировать сейчас
      </button>
    </div>
    {summary.lastError && <small>{summary.lastError}</small>}
    {conflicts.map((conflict) => <article className="mobile-sync-conflict" key={conflict.id}>
      <strong>Конфликт задачи №{conflict.entity_id}</strong>
      <p>Ни одна версия не была затёрта. Выберите итог для полей: {conflict.conflicting_fields.join(", ")}.</p>
      {conflict.conflicting_fields.map((field) => <div className="mobile-sync-versions" key={field}>
        <span><b>{field}</b></span>
        <span>Офлайн: {value(conflict.local_values[field])}</span>
        <span>Сервер: {value(conflict.server_values[field])}</span>
      </div>)}
      <div className="mobile-sync-actions">
        <button type="button" className="secondary" onClick={() => onResolve(conflict.id, "keep_server")}>Оставить серверную</button>
        <button type="button" onClick={() => onResolve(conflict.id, "apply_local")}>Применить офлайн</button>
        <button type="button" className="secondary" onClick={() => onResolve(conflict.id, "latest_write_wins")}>Выбрать последнюю по времени</button>
      </div>
      <small>При выборе по времени приложение создаст уведомление о перезаписанной версии.</small>
    </article>)}
  </section>;
}
