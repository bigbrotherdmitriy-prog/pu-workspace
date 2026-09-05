import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppErrorBoundary } from "./AppErrorBoundary";

function Broken(): never {
  throw new Error("synthetic render failure");
}

describe("AppErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("replaces a blank application with a safe recovery action", () => {
    render(<AppErrorBoundary><Broken /></AppErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("Интерфейс нужно обновить");
    expect(screen.getByRole("button", { name: "Обновить приложение" })).toBeVisible();
  });

  it("removes only PU Workspace caches and its scoped worker before reload", async () => {
    const deleteCache = vi.fn().mockResolvedValue(true);
    const unregister = vi.fn().mockResolvedValue(true);
    Object.defineProperty(window, "caches", { configurable: true, value: { keys: vi.fn().mockResolvedValue(["pu-workspace-shell-v2", "other-app"]), delete: deleteCache } });
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { getRegistrations: vi.fn().mockResolvedValue([{ scope: "https://example.test/new/", unregister }, { scope: "https://example.test/other/", unregister: vi.fn() }]) } });

    render(<AppErrorBoundary><Broken /></AppErrorBoundary>);
    fireEvent.click(screen.getByRole("button", { name: "Обновить приложение" }));

    await waitFor(() => expect(deleteCache).toHaveBeenCalledWith("pu-workspace-shell-v2"));
    expect(deleteCache).not.toHaveBeenCalledWith("other-app");
    expect(unregister).toHaveBeenCalledOnce();
  });
});
