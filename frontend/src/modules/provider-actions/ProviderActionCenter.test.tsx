import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProviderActionCenter } from "./ProviderActionCenter";
import {
  canRequestReconciliation,
  parseProviderActionList,
  parseReconciliationResult,
  shouldPollProviderActions,
} from "./providerActionReadModel";

const action = {
  action_id: "google-task-101",
  revision: 2,
  project_id: 7,
  provider: "google_workspace",
  action_kind: "google.tasks.upsert",
  mode: "CONFIRM",
  reversibility: "REVERSIBLE",
  business_status: "requires_reconciliation",
  approval_status: "granted",
  is_current_revision: true,
  dispatch: { job_id: 31, status: "completed", progress: 100, attempts: 1, max_attempts: 3, duration_ms: 170 },
  reconciliation_status: "required",
  reconciliation: null,
  receipt_id: 41,
  receipt_outcome: "UNKNOWN",
  receipt_late: false,
  retry_state: "none",
  safe_reason: "timeout_after_effect",
  created_at: "2026-09-05T08:30:00Z",
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "provider-test" },
  });
}

function list(items: unknown[] = [action]) {
  return { items, count: items.length };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("provider action read model", () => {
  it("parses the allowlisted project-scoped projection", () => {
    const parsed = parseProviderActionList(list(), 7);
    expect(parsed[0]).toMatchObject({ actionId: "google-task-101", revision: 2, projectId: 7,
      businessStatus: "requires_reconciliation", receiptOutcome: "UNKNOWN" });
    expect(canRequestReconciliation(parsed[0])).toBe(true);
  });

  it("fails closed on a cross-project row, unknown status or extra provider material", () => {
    expect(() => parseProviderActionList(list([{ ...action, project_id: 8 }]), 7)).toThrow();
    expect(() => parseProviderActionList(list([{ ...action, business_status: "mystery" }]), 7)).toThrow();
    expect(() => parseProviderActionList(list([{ ...action, provider_response: "secret" }]), 7)).toThrow();
  });

  it("accepts only the exact reconciliation identity", () => {
    const parsed = parseProviderActionList(list(), 7)[0];
    expect(parseReconciliationResult({ action_id: "google-task-101", revision: 2, job_id: 55,
      already_queued: false }, parsed)).toEqual({ actionId: "google-task-101", revision: 2,
      jobId: 55, alreadyQueued: false });
    expect(() => parseReconciliationResult({ action_id: "other", revision: 2, job_id: 55,
      already_queued: false }, parsed)).toThrow();
  });

  it("does not allow reconcile for historical, synthetic or already-running revisions", () => {
    const parsed = parseProviderActionList(list(), 7)[0];
    expect(canRequestReconciliation({ ...parsed, isCurrentRevision: false })).toBe(false);
    expect(canRequestReconciliation({ ...parsed, provider: "synthetic" })).toBe(false);
    expect(canRequestReconciliation({ ...parsed, reconciliationStatus: "running" })).toBe(false);
    expect(canRequestReconciliation({ ...parsed, reconciliationStatus: "dead_letter" })).toBe(false);
    expect(shouldPollProviderActions([{ ...parsed, reconciliationStatus: "running" }])).toBe(true);
    expect(shouldPollProviderActions([parsed])).toBe(false);
  });
});

describe("ProviderActionCenter", () => {
  it("renders loading, empty and safe error states", async () => {
    let finish: ((value: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const view = render(<ProviderActionCenter projectId={7} />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем");
    finish?.(response(list([])));
    expect(await screen.findByText("Внешних действий по проекту пока нет")).toBeInTheDocument();

    vi.stubGlobal("fetch", vi.fn(async () => response({ payload: "not-an-allowlisted-envelope" })));
    view.rerender(<ProviderActionCenter projectId={8} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("безопасные статусы");
    expect(screen.queryByText("not-an-allowlisted-envelope")).not.toBeInTheDocument();
  });

  it("shows business, retry, UNKNOWN, reconciliation and receipt state without provider material", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(list([{ ...action,
      reconciliation_status: "retrying",
      retry_state: "retrying",
      reconciliation: { job_id: 77, status: "retrying", progress: 30, attempts: 2, max_attempts: 3, duration_ms: null },
    }]))));
    render(<ProviderActionCenter projectId={7} />);
    expect(await screen.findByText("Результат требует проверки")).toBeInTheDocument();
    expect(screen.getAllByText("retrying", { selector: "dd" })).toHaveLength(2);
    expect(screen.getByText("№ 41: UNKNOWN")).toBeInTheDocument();
    expect(screen.getByText("Проверка уже выполняется, требует оператора очереди либо доступна только для текущей ревизии.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Проверить результат" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("payload");
    expect(document.body.textContent).not.toContain("provider_response");
  });

  it("queues only the exact current action revision, once, then refreshes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      if (options?.method === "POST") return response({ action_id: "google-task-101", revision: 2,
        job_id: 55, already_queued: false });
      return response(list());
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderActionCenter projectId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByText("Проверка результата поставлена в очередь: задание № 55.")).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([, options]) => options?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(String(posts[0][0])).toBe("/provider-actions/google-task-101/revisions/2/reconcile");
    expect(posts[0][1]?.body).toBe("{}");
  });

  it("encodes the exact action id in the reconciliation path", async () => {
    const encoded = { ...action, action_id: "google/action 101" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => options?.method === "POST"
      ? response({ action_id: "google/action 101", revision: 2, job_id: 56, already_queued: true })
      : response(list([encoded])));
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderActionCenter projectId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByText("Проверка результата уже выполняется: задание № 56.")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls.find(([, options]) => options?.method === "POST")?.[0]))
      .toContain("google%2Faction%20101/revisions/2/reconcile");
  });

  it("surfaces 409 safely and does not retry the mutation", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => options?.method === "POST"
      ? response({ detail: "internal-sensitive-provider-state" }, 409)
      : response(list()));
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderActionCenter projectId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Состояние действия уже изменилось");
    expect(screen.queryByText("internal-sensitive-provider-state")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));
  });

  it("ignores a late response from the previously selected project", async () => {
    let finishFirst: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes("project_id=7")) {
        return new Promise<Response>((resolve) => { finishFirst = resolve; });
      }
      return Promise.resolve(response(list([{ ...action, action_id: "project-8", project_id: 8 }])));
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ProviderActionCenter projectId={7} />);
    view.rerender(<ProviderActionCenter projectId={8} />);
    expect(await screen.findByText("Обновление Google Tasks")).toBeInTheDocument();
    finishFirst?.(response(list([{ ...action, action_id: "stale-project-7" }])));
    await waitFor(() => expect(document.body.textContent).not.toContain("stale-project-7"));
  });

  it("does not apply a reconciliation result after the project changes", async () => {
    let finishPost: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, options?: RequestInit) => {
      if (options?.method === "POST") {
        return new Promise<Response>((resolve) => { finishPost = resolve; });
      }
      const projectId = String(input).includes("project_id=8") ? 8 : 7;
      return Promise.resolve(response(list([{ ...action, project_id: projectId,
        action_id: projectId === 8 ? "project-8" : action.action_id }]))) ;
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ProviderActionCenter projectId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    view.rerender(<ProviderActionCenter projectId={8} />);
    expect(await screen.findByText("project-8")).toBeInTheDocument();
    finishPost?.(response({ action_id: "google-task-101", revision: 2, job_id: 55, already_queued: false }));
    await waitFor(() => expect(screen.queryByText(/задание № 55/)).not.toBeInTheDocument());
  });

  it("follows the durable reconciliation from queued to its safe resolved receipt", async () => {
    let listCalls = 0;
    const queued = { ...action, reconciliation_status: "queued", retry_state: "none",
      reconciliation: { job_id: 55, status: "queued", progress: 0, attempts: 0, max_attempts: 3, duration_ms: null } };
    const resolved = { ...action, business_status: "completed", reconciliation_status: "resolved",
      reconciliation: { job_id: 55, status: "completed", progress: 100, attempts: 1, max_attempts: 3, duration_ms: 80 },
      receipt_id: 42, receipt_outcome: "APPLIED", receipt_late: true, safe_reason: null };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
      if (options?.method === "POST") return response({ action_id: action.action_id, revision: 2,
        job_id: 55, already_queued: false });
      listCalls += 1;
      return response(list([listCalls === 1 ? action : listCalls === 2 ? queued : resolved]));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProviderActionCenter projectId={7} pollIntervalMs={250} />);
    fireEvent.click(await screen.findByRole("button", { name: "Проверить результат" }));
    expect(await screen.findByText("queued", { selector: "dd" })).toBeInTheDocument();
    expect(await screen.findByText("Выполнено", {}, { timeout: 1_500 })).toBeInTheDocument();
    expect(screen.getByText("№ 42: APPLIED")).toBeInTheDocument();
    expect(screen.getByText("Квитанция получена позднее и сохранена в истории.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  });
});
