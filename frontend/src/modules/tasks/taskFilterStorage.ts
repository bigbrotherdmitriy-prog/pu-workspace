// Kept out of TasksModule so the app shell can read it without pulling the
// lazily loaded register into the main bundle.
export const TASK_FILTER_STORAGE_KEY = "pu-tasks-filter";
export const TASK_FILTERS: Array<[string, string]> = [
  ["mine", "Мои активные"], ["open", "Открытые"], ["overdue", "Просроченные"], ["review", "На проверку"], ["all", "Все"],
];

/** The filter last chosen on this device; «Мои активные» by default. */
export function storedTaskFilter(): string {
  try {
    const value = window.localStorage.getItem(TASK_FILTER_STORAGE_KEY);
    if (value && TASK_FILTERS.some(([id]) => id === value)) return value;
  } catch { /* storage unavailable: fall back to the default */ }
  return "mine";
}

export function rememberTaskFilter(filter: string) {
  try { window.localStorage.setItem(TASK_FILTER_STORAGE_KEY, filter); } catch { /* best effort */ }
}
