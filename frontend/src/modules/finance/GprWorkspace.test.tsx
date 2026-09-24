import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GprWorkspace } from "./GprWorkspace";
import type { FinanceOverview } from "./types";

const finance = {
  baselines: [
    { id: 10, contract_id: 4, name: "ГПР 02.09.2026", version: 2, status: "draft", source_format: "mpp", source_file_name: "ГПР 02.09.2026.mpp", source_sha256: "abcdef1234567890" },
    { id: 9, contract_id: 4, name: "Утверждённый ГПР", version: 1, status: "approved" },
  ],
  schedule: [
    { id: 1, baseline_id: 10, title: "Подготовка", sort_order: 1, duration_days: 3, planned_start: "2026-09-01", planned_finish: "2026-09-03", planned_progress: 100, actual_progress: 100, status: "completed" },
    { id: 2, baseline_id: 10, parent_id: 1, title: "Монтаж", sort_order: 2, duration_days: 7, predecessor_ids: "1", planned_start: "2026-09-04", planned_finish: "2026-09-10", planned_progress: 50, actual_progress: 20, status: "in_progress" },
    { id: 101, baseline_id: 9, title: "Подготовка", sort_order: 1, duration_days: 3, planned_start: "2026-09-01", planned_finish: "2026-09-03", planned_progress: 100, actual_progress: 0, status: "planned" },
    { id: 102, baseline_id: 9, parent_id: 101, title: "Монтаж", sort_order: 2, duration_days: 6, predecessor_ids: "101", planned_start: "2026-09-04", planned_finish: "2026-09-09", planned_progress: 50, actual_progress: 0, status: "planned" },
  ],
} as FinanceOverview;
const contracts = [{ id: 4, number: "Д-4", title: "Монтаж", contract_kind: "supply" }];

afterEach(cleanup);

describe("GprWorkspace", () => {
  it("shows the task grid and saves edits to the selected task", async () => {
    const onUpdateTask = vi.fn().mockResolvedValue(undefined);
    render(<GprWorkspace projectId={1} finance={finance} contracts={contracts} selectedContractId={4} onSelectContract={vi.fn()} onPrepare={vi.fn()} onUpdateTask={onUpdateTask} onBulkUpdate={vi.fn().mockResolvedValue(undefined)} onCloneBaseline={vi.fn().mockResolvedValue(11)} onImported={vi.fn()} />);

    expect(screen.getByText("Монтаж")).toBeInTheDocument();
    expect(screen.getByText("Критический путь")).toBeInTheDocument();
    expect(screen.getByText("ГПР 02.09.2026.mpp")).toBeInTheDocument();
    expect(screen.getByText(/SHA-256 abcdef123456/)).toBeInTheDocument();
    expect(screen.getByLabelText("Связи задач")).toBeInTheDocument();
    expect(screen.getAllByText("0 дн.").length).toBeGreaterThan(0);
    expect(screen.getByText("+1 дн.")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Свернуть ветвь"));
    expect(screen.queryByText("Монтаж")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Развернуть ветвь"));
    fireEvent.click(screen.getByText("Монтаж"));
    const title = await screen.findByLabelText("Название задачи");
    fireEvent.change(title, { target: { value: "Монтаж ИБП" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(onUpdateTask).toHaveBeenCalledWith(2, expect.objectContaining({ title: "Монтаж ИБП" })));
  });

  it("exports GPR explicitly and opens linked DDS operations", () => {
    const createObjectUrl = vi.fn(() => "blob:gpr");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const onOpenCashFlow = vi.fn();
    const linked = { ...finance, cash_flow: [{ id: 44, schedule_item_id: 2, direction: "outflow", title: "Монтаж", planned_date: "2026-09-15", planned_amount: 100, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1 }] } as FinanceOverview;
    render(<GprWorkspace projectId={1} finance={linked} contracts={contracts} selectedContractId={4} onSelectContract={vi.fn()} onPrepare={vi.fn()} onUpdateTask={vi.fn().mockResolvedValue(undefined)} onBulkUpdate={vi.fn().mockResolvedValue(undefined)} onCloneBaseline={vi.fn().mockResolvedValue(11)} onImported={vi.fn()} onOpenCashFlow={onOpenCashFlow} />);

    fireEvent.click(screen.getByRole("button", { name: "Экспорт ГПР CSV" }));
    expect(createObjectUrl).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "1 →" }));
    expect(onOpenCashFlow).toHaveBeenCalledWith(2);
    click.mockRestore();
  });

  it("does not show a baseline from an unrelated contract while no contract is selected", () => {
    const multiple = {
      ...finance,
      baselines: [...finance.baselines, { id: 20, contract_id: 5, name: "Другой ГПР", version: 1, status: "draft" }],
      schedule: [...finance.schedule, { id: 200, baseline_id: 20, title: "Чужая задача", sort_order: 1, planned_progress: 0, actual_progress: 0, status: "planned" }],
    } as FinanceOverview;
    render(<GprWorkspace projectId={1} finance={multiple} contracts={[...contracts, { id: 5, number: "Д-5", title: "Другой" }]} selectedContractId={0} onSelectContract={vi.fn()} onPrepare={vi.fn()} onUpdateTask={vi.fn()} onBulkUpdate={vi.fn()} onCloneBaseline={vi.fn()} onImported={vi.fn()} />);

    expect(screen.getAllByText("Выберите договор")).toHaveLength(2);
    expect(screen.queryByText("Монтаж")).not.toBeInTheDocument();
    expect(screen.queryByText("Чужая задача")).not.toBeInTheDocument();
  });

  it("selects the only contract that actually has an imported schedule", async () => {
    const onSelectContract = vi.fn();
    const productionShape = {
      ...finance,
      baselines: [{ id: 11, contract_id: 5, name: "Пустой draft", version: 3, status: "draft" }, ...finance.baselines],
    } as FinanceOverview;
    render(<GprWorkspace projectId={1} finance={productionShape} contracts={[...contracts, { id: 5, number: "Д-5", title: "Пустой" }]} selectedContractId={0} onSelectContract={onSelectContract} onPrepare={vi.fn()} onUpdateTask={vi.fn()} onBulkUpdate={vi.fn()} onCloneBaseline={vi.fn()} onImported={vi.fn()} />);

    await waitFor(() => expect(onSelectContract).toHaveBeenCalledWith(4));
  });

  it("prefers a populated Project-backed version over an empty draft", () => {
    const withEmptyLatest = {
      ...finance,
      baselines: [{ id: 11, contract_id: 4, name: "Пустой draft", version: 3, status: "draft" }, ...finance.baselines],
    } as FinanceOverview;
    render(<GprWorkspace projectId={1} finance={withEmptyLatest} contracts={contracts} selectedContractId={4} onSelectContract={vi.fn()} onPrepare={vi.fn()} onUpdateTask={vi.fn()} onBulkUpdate={vi.fn()} onCloneBaseline={vi.fn()} onImported={vi.fn()} />);

    expect(screen.getByLabelText("Версия ГПР")).toHaveValue("10");
    expect(screen.getByText("ГПР 02.09.2026.mpp")).toBeInTheDocument();
    expect(screen.getByText("Монтаж")).toBeInTheDocument();
  });
});
