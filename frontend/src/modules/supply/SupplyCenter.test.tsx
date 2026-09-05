import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SupplyCenter } from "./SupplyCenter";
import { parseProjectOrganization, parseSupplyEvidenceOptions, parseSupplyList } from "./supplyReadModel";

const supplyItem = {
  id: 1,
  recordVersion: 3,
  title: "Синтетическое оборудование",
  supplier: "Тестовый поставщик",
  status: "request_pending_approval",
  reviewState: "verified",
  requestedQuantity: "10.000",
  orderedQuantity: "0.000",
  deliveredQuantity: "0.000",
  acceptedQuantity: "0.000",
  unit: "шт",
  unitPrice: "100.25",
  currency: "RUB",
  projectId: 7,
  contractId: 5,
  scheduleBaselineId: 6,
  scheduleBaselineVersion: 2,
  scheduleItemId: 8,
  taskId: 9,
  documentVersionId: 10,
  evidenceId: "00000000-0000-4000-8000-000000000010",
  evidenceRevision: 1,
  sourceVersionId: "00000000-0000-4000-8000-000000000011",
  discrepancyCode: null,
  externalActionStatus: "not_created",
};

const evidenceOption = {
  evidenceId: "00000000-0000-4000-8000-000000000020",
  evidenceRevision: 1,
  sourceVersionId: "00000000-0000-4000-8000-000000000021",
  documentVersionId: 22,
  assessmentVersion: 3,
  verification: "verified",
  confidence: 0.96,
  locator: { kind: "page", page: 2 },
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "supply-test" },
  });
}

function apiMock(item: Record<string, unknown> = supplyItem) {
  return vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input);
    if (url === "/projects/7" && (!options?.method || options.method === "GET")) {
      return response({ id: 7, name: "Synthetic", organization_id: 4, archived_at: null });
    }
    if (url === "/api/mvp4/supply?project_id=7" && (!options?.method || options.method === "GET")) {
      return response({ items: [item], total: 1 });
    }
    if (url === "/api/v54/evidence?project_id=7" && (!options?.method || options.method === "GET")) {
      return response({ projectId: 7, items: [evidenceOption], total: 1 });
    }
    if (url.includes("/approve-request?") && options?.method === "POST") {
      return response({ supply_case_id: 1, status: "request_approved", record_version: 4,
        already_applied: false, external_action_created: false });
    }
    return response({ detail: "unexpected request" }, 500);
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("supply read model", () => {
  it("accepts only the selected project and exact evidence state", () => {
    expect(parseProjectOrganization({ id: 7, organization_id: 4 }, 7)).toBe(4);
    expect(parseSupplyList({ items: [supplyItem], total: 1 }, 7)[0]).toMatchObject({
      id: 1,
      projectId: 7,
      evidenceRevision: 1,
      externalActionStatus: "not_created",
    });
    expect(() => parseSupplyList({ items: [{ ...supplyItem, projectId: 8 }], total: 1 }, 7)).toThrow("scope mismatch");
    expect(() => parseSupplyList({ items: [{ ...supplyItem, externalActionStatus: "created" }], total: 1 }, 7)).toThrow("unsafe");
    expect(parseSupplyEvidenceOptions({ projectId: 7, items: [evidenceOption], total: 1 }, 7)[0]).toEqual(evidenceOption);
    expect(() => parseSupplyEvidenceOptions({ projectId: 8, items: [], total: 0 }, 7)).toThrow("invalid envelope");
    expect(() => parseSupplyEvidenceOptions({ projectId: 7, items: [{ ...evidenceOption, evidenceRevision: 2 }], total: 1 }, 7)).toThrow("unsafe");
  });
});

describe("SupplyCenter", () => {
  it("loads project-scoped supply chains and exposes a manager approval", async () => {
    vi.stubGlobal("fetch", apiMock());
    render(<SupplyCenter projectId={7} canEdit canManage />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаю");
    expect(await screen.findByText("Синтетическое оборудование")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Согласовать заявку" })).toBeInTheDocument();
  });

  it("binds an approval to recordVersion and one idempotency key", async () => {
    const fetchMock = apiMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<SupplyCenter projectId={7} canEdit canManage />);
    fireEvent.click(await screen.findByRole("button", { name: "Согласовать заявку" }));
    expect(await screen.findByText(/Решение сохранено/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    expect(post).toBeDefined();
    const [url, options] = post!;
    expect(String(url)).toContain("organization_id=4&project_id=7");
    const headers = options?.headers as Record<string, string>;
    const body = JSON.parse(String(options?.body)) as { command_key: string; expected_version: number };
    expect(body.expected_version).toBe(3);
    expect(headers["Idempotency-Key"]).toBe(body.command_key);
    expect(fetchMock.mock.calls.filter(([, request]) => request?.method === "POST")).toHaveLength(1);
  });

  it("fails closed when an action needs a newly selected evidence pin", async () => {
    const fetchMock = apiMock({ ...supplyItem, status: "order_approved" });
    vi.stubGlobal("fetch", fetchMock);
    render(<SupplyCenter projectId={7} canEdit canManage={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "Зафиксировать размещение" }));
    expect(await screen.findByLabelText("Форма действия снабжения")).toBeInTheDocument();
    fireEvent.submit(screen.getByLabelText("Форма действия снабжения"));
    expect(screen.getByRole("alert")).toHaveTextContent("Выберите проверенное точное доказательство");
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
  });

  it("uses one exact selected evidence and the same idempotency key in header and body", async () => {
    const fetchMock = apiMock({ ...supplyItem, status: "order_approved" });
    fetchMock.mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url === "/projects/7") return response({ id: 7, organization_id: 4 });
      if (url === "/api/mvp4/supply?project_id=7") return response({ items: [{ ...supplyItem, status: "order_approved" }], total: 1 });
      if (url === "/api/v54/evidence?project_id=7") return response({ projectId: 7, items: [evidenceOption], total: 1 });
      if (url.includes("/record-order?") && options?.method === "POST") return response({
        supply_case_id: 1, status: "order_recorded", record_version: 4,
        already_applied: false, external_action_created: false,
      });
      return response({ detail: "unexpected request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SupplyCenter projectId={7} canEdit canManage={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "Зафиксировать размещение" }));
    const select = await screen.findByLabelText("Точное доказательство");
    await waitFor(() => expect(screen.getByRole("option", { name: /Документ v22/ })).toBeInTheDocument());
    fireEvent.change(select, { target: { value: evidenceOption.evidenceId } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить решение" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
    const [, options] = fetchMock.mock.calls.find(([, request]) => request?.method === "POST")!;
    const headers = options?.headers as Record<string, string>;
    const body = JSON.parse(String(options?.body)) as Record<string, unknown>;
    expect(body.command_key).toBe(headers["Idempotency-Key"]);
    expect(body.expected_version).toBe(3);
    expect(body.evidence).toEqual({
      evidence_id: evidenceOption.evidenceId,
      evidence_revision: 1,
      source_version_id: evidenceOption.sourceVersionId,
      document_version_id: evidenceOption.documentVersionId,
    });
  });

  it("does not expose editor actions to a project viewer", async () => {
    vi.stubGlobal("fetch", apiMock({ ...supplyItem, status: "order_approved" }));
    render(<SupplyCenter projectId={7} canEdit={false} canManage={false} />);
    await screen.findByText("Синтетическое оборудование");
    expect(screen.queryByRole("button", { name: "Зафиксировать размещение" })).not.toBeInTheDocument();
    expect(screen.getByText("Действий сейчас нет")).toBeInTheDocument();
  });

  it("shows an empty state and a safe invalid-response error", async () => {
    const emptyFetch = vi.fn(async (input: RequestInfo | URL) => String(input) === "/projects/7"
      ? response({ id: 7, organization_id: 4 })
      : response({ items: [], total: 0 }));
    vi.stubGlobal("fetch", emptyFetch);
    const { rerender } = render(<SupplyCenter projectId={7} canEdit={false} canManage={false} />);
    expect(await screen.findByText("Цепочек снабжения пока нет")).toBeInTheDocument();

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/projects/8"
      ? response({ id: 8, organization_id: 4 })
      : response({ items: [{ ...supplyItem, projectId: 7 }], total: 1 })));
    rerender(<SupplyCenter projectId={8} canEdit={false} canManage={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("неожиданном формате");
  });

  it("surfaces a version conflict without retrying the POST", async () => {
    const fetchMock = apiMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (options?.method === "POST") return response({ detail: "version_conflict" }, 409);
      if (url === "/projects/7") return response({ id: 7, organization_id: 4 });
      return response({ items: [supplyItem], total: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SupplyCenter projectId={7} canEdit canManage />);
    fireEvent.click(await screen.findByRole("button", { name: "Согласовать заявку" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Запись уже изменилась");
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));
  });
});
