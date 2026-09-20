import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("API structured errors", () => {
  it("preserves a stable conflict code for truthful recovery UI", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: { code: "record_version_conflict" } }),
      { status: 409, headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" } },
    )));
    await expect(api("/management/meetings/7/source-binding", { method: "POST" }))
      .rejects.toMatchObject({
        status: 409,
        code: "record_version_conflict",
        requestId: "request-1",
      });
  });
});
