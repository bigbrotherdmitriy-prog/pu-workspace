// Provisional policy recorded in DECISIONS.md.
export type LogoutPurgeScope = "all_local_data" | "current_actor";
export function logoutPurgePolicy(value: unknown = import.meta.env.VITE_OFFLINE_LOGOUT_PURGE): LogoutPurgeScope {
  if (value === undefined || value === "" || value === "all_local_data") return "all_local_data";
  if (value === "current_actor") return value;
  throw new Error("Некорректная настройка очистки локальных данных");
}
export function logoutPurgeWarning(scope: LogoutPurgeScope): string {
  return scope === "all_local_data"
    ? "При выходе будут удалены несинхронизированные локальные изменения всех пользователей и проектов на этом устройстве. Выйти?"
    : "При выходе будут удалены несинхронизированные локальные изменения вашей учётной записи во всех проектах на этом устройстве. Выйти?";
}
