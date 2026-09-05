export type CleanupStatus = {
  job_id: number;
  status: "queued" | "running" | "retrying" | "completed" | "failed" | "dead_letter" | "cancelled";
  progress: number;
  trashed?: number;
  message?: string;
  originals_affected: false;
};

type Api = <T = unknown>(path: string, options?: RequestInit) => Promise<T>;

const TERMINAL = new Set(["completed", "failed", "dead_letter", "cancelled"]);

export async function waitForSafeCopyCleanup(
  request: Api,
  projectId: number,
  jobId: number,
  options: { attempts?: number; delayMs?: number } = {},
): Promise<CleanupStatus> {
  const attempts = options.attempts ?? 90;
  const delayMs = options.delayMs ?? 1000;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const result = await request<CleanupStatus>(`/projects/${projectId}/safe-copies/cleanup/${jobId}`);
    if (!result || result.job_id !== jobId || result.originals_affected !== false) {
      throw new Error("Ответ очистки не подтверждает сохранность оригиналов.");
    }
    if (TERMINAL.has(result.status)) {
      if (result.status !== "completed") throw new Error("Не удалось безопасно очистить копии. Повторите из журнала заданий.");
      if (!Number.isSafeInteger(result.trashed) || (result.trashed ?? -1) < 0) {
        throw new Error("Результат очистки ещё не подтверждён.");
      }
      return result;
    }
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
  }
  throw new Error("Очистка продолжается в фоне. Статус доступен в журнале заданий.");
}
