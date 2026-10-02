import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("API structured errors", () => {
  it("preserves every rejected DDS row for the atomic confirmation report", async () => {
    const detail = { code: "CASH_FLOW_BATCH_REJECTED", confirmed_count: 0,
      rows: [{ id: 1, code: "NOT_APPLIED" }, { id: 2, code: "CASH_FLOW_VERSION_MISMATCH" }] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail }),
      { status: 409, headers: { "Content-Type": "application/json" } })));
    await expect(api("/execution/cash-flow/confirm-batch", { method: "POST" })).rejects.toMatchObject({ details: detail });
  });
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
