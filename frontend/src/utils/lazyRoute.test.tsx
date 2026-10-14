import { describe, it, expect, beforeEach, vi } from "vitest";

const reloadMock = vi.fn();

beforeEach(() => {
  reloadMock.mockReset();
  window.sessionStorage.clear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload: reloadMock },
  });
});

async function importFresh() {
  vi.resetModules();
  return await import("./lazyRoute");
}

describe("lazyRoute chunk error handling", () => {
  it("reloads on chunk load error", async () => {
    const { handleChunkLoadError } = await importFresh();
    expect(() =>
      handleChunkLoadError(new Error("Failed to fetch dynamically imported module")),
    ).not.toThrow();
    expect(reloadMock).toHaveBeenCalledTimes(1);
  });

  it("does not reload twice within 30s", async () => {
    const { handleChunkLoadError } = await importFresh();
    handleChunkLoadError(new Error("Failed to fetch dynamically imported module"));
    expect(reloadMock).toHaveBeenCalledTimes(1);
    expect(() =>
      handleChunkLoadError(new Error("Failed to fetch dynamically imported module")),
    ).toThrow();
    expect(reloadMock).toHaveBeenCalledTimes(1);
  });

  it("does not reload on non-chunk error", async () => {
    const { handleChunkLoadError } = await importFresh();
    expect(() => handleChunkLoadError(new Error("Boom"))).toThrow("Boom");
    expect(reloadMock).not.toHaveBeenCalled();
  });
});
