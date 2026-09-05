import { afterEach, describe, expect, it, vi } from "vitest";
import { waitForSafeCopyCleanup } from "./safeCopyCleanup";

describe("managed safe-copy cleanup", () => {
  afterEach(() => vi.useRealTimers());

  it("waits for the durable receipt and exposes the canonical completion message", async () => {
    vi.useFakeTimers();
    const api = vi.fn()
      .mockResolvedValueOnce({ job_id: 9, status: "running", progress: 50, originals_affected: false })
      .mockResolvedValueOnce({
        job_id: 9, status: "completed", progress: 100, trashed: 2,
        message: "Копии удалены, можете архивировать проект", originals_affected: false,
      });
    const pending = waitForSafeCopyCleanup(api, 7, 9, { attempts: 3, delayMs: 5 });
    await vi.advanceTimersByTimeAsync(5);
    await expect(pending).resolves.toMatchObject({ status: "completed", trashed: 2 });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("fails closed on dead-letter and never reports success", async () => {
    const api = vi.fn().mockResolvedValue({
      job_id: 9, status: "dead_letter", progress: 80, originals_affected: false,
    });
    await expect(waitForSafeCopyCleanup(api, 7, 9, { attempts: 1, delayMs: 0 }))
      .rejects.toThrow("Не удалось безопасно очистить копии");
  });

  it("rejects a response that does not prove originals were preserved", async () => {
    const api = vi.fn().mockResolvedValue({
      job_id: 9, status: "completed", progress: 100, trashed: 1, originals_affected: true,
    });
    await expect(waitForSafeCopyCleanup(api, 7, 9, { attempts: 1, delayMs: 0 }))
      .rejects.toThrow("не подтверждает сохранность оригиналов");
  });

  it("rejects completion without a confirmed count", async () => {
    const api = vi.fn().mockResolvedValue({job_id: 9, status: "completed", originals_affected: false});
    await expect(waitForSafeCopyCleanup(api, 7, 9)).rejects.toThrow("ещё не подтверждён");
  });

  it("rejects another job's result", async () => {
    const api = vi.fn().mockResolvedValue({job_id: 10, status: "completed", trashed: 2, originals_affected: false});
    await expect(waitForSafeCopyCleanup(api, 7, 9)).rejects.toThrow("не подтверждает");
  });
});
