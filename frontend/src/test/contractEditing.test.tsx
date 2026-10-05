import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { App } from "../App";

vi.mock("../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/client")>(), api: vi.fn(),
}));
vi.mock("../modules/documents/localUploadJobs", () => ({
  localUploadMimeType: () => "text/csv", awaitLocalUploadJobs: async () => ({ documents: [91] }),
}));
vi.mock("../modules/contracts/ContractsModule", () => ({ ContractsModule: ({ children, number, title, currency, onNumberChange, onTitleChange, onCreate }: any) => <div>
  <span aria-label="Synthetic create currency">{currency}</span>
  <input aria-label="Synthetic create number" value={number} onChange={(event) => onNumberChange(event.target.value)} />
  <input aria-label="Synthetic create title" value={title} onChange={(event) => onTitleChange(event.target.value)} />
  <button onClick={onCreate}>Create synthetic contract</button>{children}
</div> }));
vi.mock("../modules/contracts/ContractScheme", async () => {
  const { useState } = await import("react");
  return { ContractScheme: ({ onDropFinance, renderDetails, contracts }: any) => {
    const [open, setOpen] = useState(true);
    const [view, setView] = useState("register");
    return <>
      <button onClick={() => onDropFinance([new File(["synthetic"], "plan.csv")], 31, "cash-flow")}>Attach synthetic finance</button>
      <button onClick={() => setOpen(!open)}>Toggle synthetic details</button>
      <button onClick={() => setView(view === "register" ? "scheme" : "register")}>Toggle synthetic view</button>
      <div key={view}>{open && contracts[0] && renderDetails?.(contracts[0])}</div>
    </>;
  } };
});
vi.mock("../modules/finance/GprDdsWorkspace", () => ({ GprDdsWorkspace: ({ dds }: any) => <div>{dds}</div> }));
vi.mock("../modules/finance/FinanceOperations", () => ({ FinanceOperations: ({ invoiceProposal, onConfirmInvoice }: any) =>
  <div>{invoiceProposal && <><span>Synthetic invoice draft</span>
    <button onClick={onConfirmInvoice}>Confirm synthetic invoice</button></>}</div>,
}));
vi.mock("../modules/finance/DdsWorkspace", () => ({ DdsWorkspace: ({ onReviewInvoice }: any) =>
  <button onClick={() => onReviewInvoice(91)}>Review synthetic invoice</button>,
}));
afterEach(cleanup);

const contract = { id: 31, record_version: 5, number: "SYN-31", title: "Synthetic contract",
  status: "active", contract_kind: "customer", amount: null, advance_amount: null,
  counterparty: null, signed_at: null, retention_percent: 22, linked_documents: [], version_history: [],
  vat_mode: "unspecified", vat_rate: null, performed_from: null, performed_to: null, warranty_until: null };
const overview = { summary: {}, baselines: [], schedule: [], budget: [], cash_flow: [], procurement: [], acts: [] };
const invoice = { id: 61, project_id: 7, source_document_id: 91, amount: 125, currency: "RUB",
  selected_cost_category_id: 1, payment_purpose: "Synthetic materials", target_kind: "cash_flow",
  status: "proposed", requires_confirmation: true };
let current: typeof contract;
let patch: (body: any) => Promise<unknown>;
let attach: () => Promise<unknown>;
let confirmInvoice: () => Promise<unknown>;
let create: () => Promise<unknown>;
let projectCurrency: string;

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  sessionStorage.setItem("pu_active_project_id", "7");
  current = { ...contract };
  patch = async (body) => { current = { ...current, ...body, record_version: current.record_version + 1 }; return current; };
  attach = async () => { current = { ...current, record_version: 6 }; return { status: "ok", record_version: 6 }; };
  confirmInvoice = async () => { throw new Error("Synthetic invoice refusal"); };
  create = async () => ({ ...current, id: 32 });
  projectCurrency = "RUB";
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (path === "/auth/me") return { id: 1, role: "owner", full_name: "QA", is_admin: true };
    if (path === "/projects/") return { projects: [{ id: 7, name: "Synthetic project", currency: projectCurrency }, { id: 8, name: "Other synthetic project" }] };
    if (path === "/projects/7/contracts/31/documents" && init?.method === "POST") return attach();
    if (path === "/projects/7/contracts/31" && init?.method === "PATCH") return patch(JSON.parse(String(init.body)));
    if (path === "/projects/7/contracts" && init?.method === "POST") return create();
    if (path === "/projects/7/contracts") return { contracts: [{ ...current }] };
    if (path.startsWith("/dashboard/project")) return { summary: { attention: 0, overdue_tasks: 0, overdue_obligations: 0 }, documents: [] };
    if (path === "/local-upload/analyze") return { jobs: [] };
    if (path.includes("/structured-preview")) throw new Error("Synthetic preview refusal after attachment");
    if (path.startsWith("/execution/overview")) return overview;
    if (path.startsWith("/execution/cost-categories")) return { categories: [] };
    if (path.startsWith("/execution/document-candidates")) return { candidates: [{ document_id: 91, name: "invoice.pdf",
      kind: "invoice", score: 98, reasons: [], hints: {}, already_linked: false, originals_changed: false }] };
    if (path.startsWith("/execution/invoice-extraction-proposals") && path.endsWith("/confirm")) return confirmInvoice();
    if (path.includes("/invoice-extraction-proposals")) return { ...invoice };
    const lists: Record<string, string> = { documents: "documents", snapshots: "snapshots", tasks: "tasks", risks: "risks",
      decisions: "decisions", "response-drafts": "drafts", inbox: "messages", proposals: "proposals", contracts: "contracts",
      members: "members", audit: "logs", automations: "rules", "project-contacts": "contacts", obligations: "obligations",
      meetings: "meetings", notifications: "notifications", "document-candidates": "candidates" };
    for (const [segment, key] of Object.entries(lists)) if (path.split(/[/?]/).includes(segment)) return { [key]: [] };
    if (path.startsWith("/integrations/")) return { adapters: [] };
    return null;
  });
});

async function openContracts() {
  render(<App />);
  await screen.findByRole("option", { name: "Synthetic project" }, { timeout: 10000 });
  fireEvent.click(screen.getByTitle("Договоры"));
  await screen.findByRole("button", { name: "Редактировать" }, { timeout: 10000 });
}
async function edit() {
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  fireEvent.change(screen.getByPlaceholderText("Сумма договора, ₽"), { target: { value: "12345.67" } });
  fireEvent.change(screen.getByPlaceholderText("Контрагент"), { target: { value: "Synthetic supplier" } });
}
function saves() { return vi.mocked(api).mock.calls.filter(([path, init]) => path === "/projects/7/contracts/31" && init?.method === "PATCH"); }

describe("contract CAS editing", () => {
  it("labels monetary editor inputs in the project currency and passes currency to creation", async () => {
    projectCurrency = "USD";
    await openContracts();
    expect(screen.getByLabelText("Synthetic create currency")).toHaveTextContent("USD");
    fireEvent.click(screen.getByRole("button", { name: "Редактировать" }));
    expect(screen.getByLabelText("Сумма редактируемого договора с НДС, USD")).toHaveAttribute("placeholder", "Сумма договора, USD");
    expect(screen.getByLabelText("Аванс редактируемого договора с НДС, USD")).toHaveAttribute("placeholder", "Аванс, USD");
    expect(screen.queryByLabelText("Сумма редактируемого договора с НДС, ₽")).not.toBeInTheDocument();
  });

  it("passes project currency to common current/history values and the budget proposal advance", async () => {
    projectCurrency = "USD";
    current = { ...current, amount: "731.23", advance_amount: "112.34", version_history: [{ id: 53, sequence: 2,
      event: "updated", changed_fields: ["amount"], occurred_at: "2026-09-30T10:00:00Z", snapshot: { amount: "71.23" } }],
      budget_proposals: [{ id: 73, contract_id: 31, contract_record_version: 5, operation: "create", amount: "731.23",
        advance_amount: "112.34", currency: "USD", description: "Synthetic USD total", status: "proposed" }] } as any;
    await openContracts();
    expect(screen.getByText("731,23 USD")).toBeInTheDocument();
    expect(screen.getByText("112,34 USD")).toBeInTheDocument();
    expect(screen.getByText(/Общая сумма — одна строка\. Аванс 112,34 USD/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("История договора"));
    fireEvent.click(screen.getByText("Значения версии 2"));
    expect(screen.getByText("71,23 USD")).toBeVisible();
  });

  it("confirms a pending budget only by button and preserves the exact gross decimal amount", async () => {
    current = { ...current, amount: "9999999999999999.99", budget_proposals: [{ id: 72, contract_id: 31,
      contract_record_version: 5, operation: "create", amount: "9999999999999999.99", currency: "RUB",
      description: "Synthetic contract total", selected_cost_category_id: 1, status: "proposed" }] } as any;
    await openContracts();
    expect(vi.mocked(api).mock.calls.some(([path, init]) => path.startsWith("/contract-budget-proposals/") && init?.method === "POST")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить бюджет" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/contract-budget-proposals/72", expect.anything()));
    const call = vi.mocked(api).mock.calls.find(([path, init]) => path === "/contract-budget-proposals/72" && init?.method === "PATCH")!;
    expect(JSON.parse(String(call[1]?.body)).amount).toBe("9999999999999999.99");
  });

  it("uses one details surface and retains the external draft through reopen and register/scheme changes", async () => {
    await openContracts(); await edit();
    expect(screen.queryByText("Расширенное редактирование карточек")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle synthetic details" }));
    expect(screen.queryByRole("form", { name: "Редактирование договора SYN-31" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle synthetic details" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle synthetic view" }));
    expect(screen.getByPlaceholderText("Сумма договора, ₽")).toHaveValue(12345.67);
    expect(screen.getByText("Версия открытого черновика: 5")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(saves()).toHaveLength(1));
    expect(JSON.parse(String(saves()[0][1]?.body))).toMatchObject({ expected_record_version: 5 });
  });

  it("saves explicit VAT zero and separate execution/warranty dates without touching retention", async () => {
    await openContracts(); await edit();
    fireEvent.change(screen.getByLabelText("НДС договора"), { target: { value: "rate" } });
    fireEvent.change(screen.getByLabelText("Ставка НДС, %"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Начало исполнения договора"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Окончание исполнения договора"), { target: { value: "2026-12-31" } });
    fireEvent.change(screen.getByLabelText("Гарантия до"), { target: { value: "2027-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(saves()).toHaveLength(1));
    expect(JSON.parse(String(saves()[0][1]?.body))).toMatchObject({
      vat_mode: "rate", vat_rate: "0", performed_from: "2026-09-01", performed_to: "2026-12-31",
      warranty_until: "2027-12-31", retention_percent: "22",
    });
    expect(screen.getByText(/Серверная проверка финансового периода.*V6-10b/)).toBeInTheDocument();
  });

  it("preserves server warnings after a successful save", async () => {
    patch = async () => ({ ...current, warnings: [{ code: "CONTRACT_AMOUNT_UNSPECIFIED", message: "сумма договора не задана" }] });
    await openContracts(); await edit();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await screen.findByText(/Договор обновлён.*сумма договора не задана/);
  });

  it("synchronizes the attached version even when the subsequent finance preview fails", async () => {
    await openContracts();
    fireEvent.click(screen.getByRole("button", { name: "Attach synthetic finance" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/projects/7/contracts/31/documents", expect.anything()));
    await screen.findByText("Synthetic preview refusal after attachment");
    fireEvent.click(screen.getByTitle("Договоры"));
    await waitFor(() => expect(screen.getByText(/Версия карточки: 6/)).toBeInTheDocument());
    await edit();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(saves()).toHaveLength(1));
    expect(JSON.parse(String(saves()[0][1]?.body))).toMatchObject({ expected_record_version: 6, amount: "12345.67", counterparty: "Synthetic supplier" });
  });

  it("keeps the original draft version through refresh and leaves a 409 visible inside the form", async () => {
    await openContracts(); await edit();
    current = { ...current, record_version: 6 };
    fireEvent.click(screen.getByRole("button", { name: "Обновить данные проекта" }));
    await screen.findByText(/Версия карточки: 6/);
    patch = async () => { throw new ApiError("Synthetic version conflict. Код обращения: synthetic-request", 409, "synthetic-request", "RECORD_VERSION_CONFLICT"); };
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(saves()).toHaveLength(1));
    expect(JSON.parse(String(saves()[0][1]?.body))).toMatchObject({ expected_record_version: 5 });
    const form = await screen.findByRole("form", { name: "Редактирование договора SYN-31" });
    await waitFor(() => expect(within(form).getByRole("alert")).toHaveTextContent("synthetic-request"));
    expect(within(form).getByPlaceholderText("Сумма договора, ₽")).toHaveValue(12345.67);
    expect(within(form).getByPlaceholderText("Контрагент")).toHaveValue("Synthetic supplier");
    expect(screen.queryByText(/Договор обновлён/)).not.toBeInTheDocument();
    expect(saves()).toHaveLength(1);
  });

  it("loads a current comparison after conflict without rebasing or automatically saving the draft", async () => {
    await openContracts(); await edit();
    current = { ...current, record_version: 6, amount: 100 as any, counterparty: "Another synthetic supplier" as any };
    patch = async () => { throw new ApiError("Synthetic conflict", 409, "synthetic-request"); };
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить текущую карточку для сравнения" }));
    const comparison = await screen.findByRole("region", { name: "Сравнение версий договора" });
    expect(comparison).toHaveTextContent("Another synthetic supplier");
    expect(screen.getByPlaceholderText("Сумма договора, ₽")).toHaveValue(12345.67);
    expect(saves()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Сохранить изменения" })).toBeDisabled();
  });

  it("preserves the editor with an inline error on a non-conflict refusal", async () => {
    await openContracts(); await edit();
    patch = async () => { throw new ApiError("Synthetic validation refusal", 422, "synthetic-request"); };
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    const form = await screen.findByRole("form", { name: "Редактирование договора SYN-31" });
    await waitFor(() => expect(within(form).getByRole("alert")).toHaveTextContent("Synthetic validation refusal"));
    expect(within(form).getByPlaceholderText("Сумма договора, ₽")).toHaveValue(12345.67);
  });

  it("shows the server validation field and reason while preserving the unsaved draft", async () => {
    await openContracts(); await edit();
    patch = async () => { throw new ApiError("HTTP 422. Код обращения: synthetic-request", 422, "synthetic-request", undefined,
      [{ loc: ["body", "advance_amount"], msg: "Аванс не должен превышать сумму договора", type: "value_error" }]); };
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    const form = await screen.findByRole("form", { name: "Редактирование договора SYN-31" });
    await waitFor(() => expect(within(form).getByRole("alert")).toHaveTextContent("Аванс не должен превышать сумму договора"));
    expect(within(form).getByPlaceholderText("Сумма договора, ₽")).toHaveValue(12345.67);
    expect(within(form).getByPlaceholderText("Аванс, ₽")).toHaveAttribute("aria-invalid", "true");
  });

  it("disables edits and repeated saves until the current request completes", async () => {
    await openContracts(); await edit();
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    patch = () => pending;
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    expect(screen.getByRole("button", { name: "Сохраняю…" })).toBeDisabled();
    expect(screen.getByPlaceholderText("Сумма договора, ₽")).toBeDisabled();
    expect(screen.getByPlaceholderText("Контрагент")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Сохраняю…" }));
    expect(saves()).toHaveLength(1);
    await act(async () => { resolve({ ...current, record_version: 6 }); await pending; });
    await screen.findByText(/Договор обновлён/);
    expect(screen.queryByRole("form", { name: "Редактирование договора SYN-31" })).not.toBeInTheDocument();
  });

  it("ignores a save refusal delivered after switching projects", async () => {
    await openContracts(); await edit();
    let reject!: (reason: Error) => void;
    const pending = new Promise((_, fail) => { reject = fail; });
    patch = () => pending;
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() => expect(saves()).toHaveLength(1));
    fireEvent.change(screen.getByRole("combobox", { name: "Текущий проект" }), { target: { value: "8" } });
    await act(async () => { reject(new Error("Old project refusal")); await pending.catch(() => {}); });
    expect(screen.queryByText("Old project refusal")).not.toBeInTheDocument();
  });

  it("does not publish old creation feedback after switching projects", async () => {
    await openContracts();
    let reject!: (reason: Error) => void;
    const pending = new Promise((_, fail) => { reject = fail; });
    create = () => pending;
    fireEvent.change(screen.getByLabelText("Synthetic create number"), { target: { value: "NEW-SYN" } });
    fireEvent.change(screen.getByLabelText("Synthetic create title"), { target: { value: "Synthetic new title" } });
    fireEvent.click(screen.getByRole("button", { name: "Create synthetic contract" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/projects/7/contracts", expect.objectContaining({ method: "POST" })));
    fireEvent.change(screen.getByRole("combobox", { name: "Текущий проект" }), { target: { value: "8" } });
    await act(async () => { reject(new Error("Old creation project refusal")); await pending.catch(() => {}); });
    expect(screen.queryByText("Old creation project refusal")).not.toBeInTheDocument();
  });
});

describe("invoice editor feedback", () => {
  it("does not close on refusal and shows the error inside the editor and in the global feedback layer", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "Synthetic project" }, { timeout: 10000 });
    fireEvent.click(screen.getByTitle("ГПР и ДДС"));
    fireEvent.click(await screen.findByRole("button", { name: "Review synthetic invoice" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm synthetic invoice" }));
    const dialog = await screen.findByRole("dialog", { name: "Проверка финансовых данных" });
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("Synthetic invoice refusal"));
    expect(within(dialog).getByText("Synthetic invoice draft")).toBeInTheDocument();
    expect(screen.getByRole("alert", { name: "Ошибка операции" })).toHaveTextContent("Synthetic invoice refusal");
    expect(screen.getByRole("alert", { name: "Ошибка операции" }).closest(".app-feedback-layer")).not.toBeNull();
  });

  it("closes after a real successful confirmation without reopening a confirmed proposal", async () => {
    confirmInvoice = async () => ({ ...invoice, status: "confirmed", requires_confirmation: false });
    render(<App />);
    await screen.findByRole("option", { name: "Synthetic project" }, { timeout: 10000 });
    fireEvent.click(screen.getByTitle("ГПР и ДДС"));
    fireEvent.click(await screen.findByRole("button", { name: "Review synthetic invoice" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm synthetic invoice" }));
    await screen.findByText(/Счёт подтверждён человеком/);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Проверка финансовых данных" })).not.toBeInTheDocument());
  });
});
