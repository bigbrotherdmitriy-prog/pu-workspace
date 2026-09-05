import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseQueuedMutation, parseStorageMutationPreview, StorageMutationPanel } from "./StorageMutationPanel";

const preview = { project_id: 7, proposal_id: 11, action_id: 13, record_version: 2, kind: "rename",
  before_name: "old.pdf", after_name: "standard.pdf", provider: "google_drive", synthetic_only: true,
  execution_allowed: true, can_rollback: false };

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("storage mutation read model", () => {
  it("rejects scope mismatch, provider locators and extra response data", () => {
    expect(parseStorageMutationPreview(preview, 7, 11, 13)).toMatchObject({ record_version: 2 });
    expect(() => parseStorageMutationPreview({ ...preview, project_id: 8 }, 7, 11, 13)).toThrow();
    expect(() => parseStorageMutationPreview({ ...preview, path: "/secret" }, 7, 11, 13)).toThrow();
    const queued = { job_id: 4, project_id: 7, status: "queued", already_queued: false, record_version: 2 };
    expect(parseQueuedMutation(queued, 7)).toEqual(queued);
    expect(() => parseQueuedMutation({ ...queued, locator: "provider-id" }, 7)).toThrow();
  });
});

describe("StorageMutationPanel", () => {
  it("shows preview but hard-disables confirmation outside synthetic cohort", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...preview, execution_allowed: false })));
    render(<StorageMutationPanel projectId={7} proposalId={11} actionId={13} />);
    expect(await screen.findByText("old.pdf → standard.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить точное изменение" })).toBeDisabled();
    expect(screen.getByText(/синтетический тестовый контур/)).toBeInTheDocument();
  });

  it("submits only IDs, CAS and one idempotency key", async () => {
    const requests: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000001" });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push([String(input), init]);
      if (!init?.method) return response(preview);
      return response({ job_id: 4, project_id: 7, status: "queued", already_queued: false, record_version: 2 });
    }));
    render(<StorageMutationPanel projectId={7} proposalId={11} actionId={13} />);
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить точное изменение" }));
    await waitFor(() => expect(requests.some(([, init]) => init?.method === "POST")).toBe(true));
    const [, post] = requests.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post?.body))).toEqual({ proposal_id: 11, action_id: 13, record_version: 2 });
    expect((post?.headers as Record<string, string>)["Idempotency-Key"]).toBe("storage-ui-00000000-0000-4000-8000-000000000001");
    expect(String(post?.body)).not.toContain("path");
  });

  it("requires refresh after a 409 stale response", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000002" });
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? response({ detail: "stale" }, 409) : response(preview)));
    render(<StorageMutationPanel projectId={7} proposalId={11} actionId={13} />);
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить точное изменение" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Версия изменилась");
  });
});
