import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DdsWorkspace } from "./DdsWorkspace";
import type { FinanceOverview } from "./types";

const finance = {
  cash_flow: [
    { id: 1, contract_id: 4, direction: "inflow", title: "Этап 1", planned_date: "2026-01-29", planned_amount: 1000, actual_amount: 0, currency: "RUB", record_version: 1, object_name: "Дубна", category: "Приход от заказчика", note: "Оплата этапа", status: "approved" },
    { id: 2, contract_id: 4, direction: "outflow", title: "Щиты", planned_date: "2026-02-10", planned_amount: 400, actual_amount: 0, currency: "RUB", record_version: 3, object_name: "Общие", category: "Оборудование", note: "Аванс", status: "proposed" },
  ],
} as FinanceOverview;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("DdsWorkspace", () => {
  it("shows per-row rejection and keeps the whole selection after an atomic failure", async () => {
    const error = Object.assign(new Error("Пакет отклонён"), { details: { rows: [
      { id: 2, code: "CASH_FLOW_VERSION_MISMATCH", message: "Запись изменилась" }] } });
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()}
      onConfirmMany={vi.fn().mockRejectedValue(error)} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать операцию Щиты" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить выбранные (1)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("CASH_FLOW_VERSION_MISMATCH");
    expect(screen.getByRole("checkbox", { name: "Выбрать операцию Щиты" })).toBeChecked();
  });

  it("does not offer payment for an approved forecast", () => {
    const row = { ...finance.cash_flow[1], status: "approved", entry_kind: "plan_forecast" };
    render(<DdsWorkspace finance={{ ...finance, cash_flow: [row] } as FinanceOverview} selectedContractId={4}
      onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.queryByRole("button", { name: "Оплата" })).not.toBeInTheDocument();
    expect(screen.getByText("Прогноз")).toBeInTheDocument();
  });
  it("allows a proven matrix forecast without a schedule and labels it as forecast", () => {
    const row = { ...finance.cash_flow[1], source_document_id: 90, budget_line_id: 72,
      confirmation_allowed: true, confirmation_kind: "plan_forecast", entry_kind: "legacy_unclassified" };
    render(<DdsWorkspace finance={{ ...finance, cash_flow: [row] } as FinanceOverview} selectedContractId={4}
      onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.getByRole("checkbox", { name: "Выбрать операцию Щиты" })).toBeInTheDocument();
    expect(screen.getByText("Прогноз")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeEnabled();
    expect(screen.queryByLabelText("Этап ГПР для Щиты")).not.toBeInTheDocument();
  });
  it("imports a planned DDS workbook from the main DDS header", () => {
    const onImportCashFlow = vi.fn();
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onImportCashFlow={onImportCashFlow} />);
    const workbook = new File(["dds"], "для PU ДДС.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });

    expect(screen.getByRole("button", { name: "Импортировать плановый ДДС" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Импорт планового ДДС"), { target: { files: [workbook] } });

    expect(onImportCashFlow).toHaveBeenCalledWith([workbook]);
  });

  it("cancels an unlinked invoice only after confirmation and waits for persistence", async () => {
    let finish!: () => void;
    const onConfirm = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const unlinked = { ...finance, cash_flow: [{ ...finance.cash_flow[1], source_document_id: 90, contract_id: undefined }] } as FinanceOverview;
    const props = { finance: unlinked, selectedContractId: 0, onPrepare: vi.fn(), onConfirm, onConfirmMany: vi.fn(), onConfirmPayment: vi.fn(), onLinkControls: vi.fn() };
    const { rerender } = render(<DdsWorkspace {...props} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    const button = screen.getByRole("button", { name: "Удалить операцию Щиты" });
    fireEvent.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(button);
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("Щиты"));
    expect(onConfirm).toHaveBeenCalledWith("cash-flow", 2, "cancelled");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    finish();
    await waitFor(() => expect(button).toBeEnabled());
    rerender(<DdsWorkspace {...props} finance={{ ...unlinked, cash_flow: [{ ...unlinked.cash_flow[0], status: "cancelled" }] }} />);
    expect(screen.queryByRole("button", { name: "Удалить операцию Щиты" })).not.toBeInTheDocument();
    expect(screen.queryByText("cancelled")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Фильтр по статусу"), { target: { value: "all" } });
    expect(screen.getByText("cancelled")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Сводка" }));
    expect(screen.queryByText(/400/)).not.toBeInTheDocument();
  });

  it("does not offer cancellation for settled or cancelled operations", () => {
    const closed = { ...finance, cash_flow: [
      { ...finance.cash_flow[0], status: "received", actual_date: "2026-01-30", actual_amount: 1000 },
      { ...finance.cash_flow[1], status: "paid", actual_date: "2026-02-10", actual_amount: 400 },
      { ...finance.cash_flow[1], id: 3, status: "cancelled" },
    ] } as FinanceOverview;
    render(<DdsWorkspace finance={closed} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.queryByRole("button", { name: /Удалить операцию/ })).not.toBeInTheDocument();
  });

  it("keeps an operation available for retry when cancellation fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn().mockRejectedValue(new Error("offline"))} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить операцию Этап 1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось удалить операцию");
    expect(screen.getByRole("button", { name: "Удалить операцию Этап 1" })).toBeEnabled();
  });

  it("shows every workbook view and recalculates summaries from detail rows", () => {
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "Таблица ДДС" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Статья ДДС")).toBeInTheDocument();
    expect(screen.getByText("Итого за 2026, RUB")).toBeInTheDocument();
    expect(screen.getByText("Платежи — всего")).toBeInTheDocument();
    expect(screen.getByText("Расходы по месяцам")).toBeInTheDocument();
    expect(screen.getByText("Баланс накопленным итогом")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "ДДС по месяцам" }));
    expect(screen.getByText("январь 2026 г.")).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveClass("dds-monthly-table");
    expect(screen.getByRole("table").parentElement).toHaveClass("dds-months");
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.getByText("Оплата этапа")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Сводка" }));
    expect(screen.getByText("По объектам")).toBeInTheDocument();
    expect(screen.getByText("Расходы по статьям")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Таблица ДДС" }));
    expect(screen.getByText("Дубна — всего поступлений")).toBeInTheDocument();
  });

  it("confirms selected proposed operations in one callback", () => {
    const onConfirmMany = vi.fn();
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={onConfirmMany} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать операцию Щиты" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить выбранные (1)" }));

    expect(onConfirmMany).toHaveBeenCalledWith("cash-flow", [2], "approved");
  });

  it("links a document-derived proposal before it can be confirmed", () => {
    const onLinkControls = vi.fn();
    const unlinked = {
      ...finance,
      baselines: [{ id: 70, contract_id: 4, name: "ГПР", version: 1, status: "approved" }],
      schedule: [{ id: 71, baseline_id: 70, title: "Монтаж", planned_progress: 0, actual_progress: 0, status: "planned" }],
      budget: [{ id: 72, contract_id: 4, category: "Работы", description: "Монтаж", planned_amount: 400, committed_amount: 0, actual_amount: 0, remaining_amount: 400, overrun_amount: 0, forecast_amount: 400, currency: "RUB", status: "approved" }],
      cash_flow: [{ ...finance.cash_flow[1], id: 9, source_document_id: 90 }],
    } as FinanceOverview;
    render(<DdsWorkspace finance={unlinked} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={onLinkControls} />);

    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Этап ГПР для Щиты"), { target: { value: "71" } });
    fireEvent.change(screen.getByLabelText("Строка бюджета для Щиты"), { target: { value: "72" } });
    fireEvent.click(screen.getByRole("button", { name: "Связать с контролями" }));

    expect(onLinkControls).toHaveBeenCalledWith(9, 4, 71, 72);
  });

  it("backfills a budget line onto an already-approved row that has none (ADR-V6-05-INCOME-BUDGET-RU)", () => {
    const onLinkApprovedBudgetLine = vi.fn();
    const withIncomeBudget = {
      ...finance,
      budget: [
        { id: 80, contract_id: 4, direction: "inflow", category: "Выручка", description: "Доходная строка", planned_amount: 1000, committed_amount: 0, actual_amount: 0, remaining_amount: 1000, overrun_amount: 0, forecast_amount: 1000, currency: "RUB", status: "approved" },
        { id: 81, contract_id: 4, direction: "outflow", category: "Расходы", description: "Расходная строка", planned_amount: 400, committed_amount: 0, actual_amount: 0, remaining_amount: 400, overrun_amount: 0, forecast_amount: 400, currency: "RUB", status: "approved" },
      ],
    } as FinanceOverview;
    render(<DdsWorkspace finance={withIncomeBudget} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onLinkApprovedBudgetLine={onLinkApprovedBudgetLine} />);

    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    const select = screen.getByLabelText("Строка бюджета для Этап 1");
    // Only the matching-direction (inflow) line is offered, the outflow one is not.
    expect(within(select).queryByText("Расходная строка")).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "80" } });
    fireEvent.click(screen.getByRole("button", { name: "Привязать бюджет" }));

    expect(onLinkApprovedBudgetLine).toHaveBeenCalledWith(1, 80);
  });

  it("does not offer the backfill action when the row already has a budget line", () => {
    const onLinkApprovedBudgetLine = vi.fn();
    const linked = {
      ...finance,
      budget: [{ id: 80, contract_id: 4, direction: "inflow", category: "Выручка", description: "Доходная строка", planned_amount: 1000, committed_amount: 0, actual_amount: 0, remaining_amount: 1000, overrun_amount: 0, forecast_amount: 1000, currency: "RUB", status: "approved" }],
      cash_flow: [{ ...finance.cash_flow[0], budget_line_id: 80 }, finance.cash_flow[1]],
    } as FinanceOverview;
    render(<DdsWorkspace finance={linked} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onLinkApprovedBudgetLine={onLinkApprovedBudgetLine} />);

    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.queryByRole("button", { name: "Привязать бюджет" })).not.toBeInTheDocument();
  });

  it("exports every active workbook view as an Excel-compatible CSV", () => {
    const createObjectUrl = vi.fn(() => "blob:dds");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Таблица ДДС" }));
    fireEvent.click(screen.getByRole("tab", { name: "ДДС по месяцам" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: ДДС по месяцам" }));
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Детализация" }));
    fireEvent.click(screen.getByRole("tab", { name: "Сводка" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Сводка" }));

    expect(createObjectUrl).toHaveBeenCalledTimes(4);
    expect(click).toHaveBeenCalledTimes(4);
    expect(revokeObjectUrl).toHaveBeenCalledTimes(4);
    click.mockRestore();
  });

  it("combines equal cost names into one calendar row and sums every month", () => {
    const repeatedCosts = { ...finance, cash_flow: [
      finance.cash_flow[0],
      { ...finance.cash_flow[1], title: "ЭМ Щиты", planned_date: "2026-02-10", planned_amount: 400 },
      { ...finance.cash_flow[1], id: 3, title: "  эм   щиты ", planned_date: "2026-02-18", planned_amount: 100 },
      { ...finance.cash_flow[1], id: 4, title: "ЭМ Щиты", planned_date: "2026-03-05", planned_amount: 600 },
    ] } as FinanceOverview;

    render(<DdsWorkspace finance={repeatedCosts} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    const titles = screen.getAllByText("ЭМ Щиты", { exact: true });
    expect(titles).toHaveLength(1);
    const row = titles[0].closest("tr")!;
    expect(within(row).getByText(/1\s?100,00/)).toBeInTheDocument();
    expect(within(row).getByTitle("Сумма 2 операций")).toHaveTextContent(/500,00/);
    expect(within(row).getByText("2 операций")).toBeInTheDocument();
    expect(within(row).getByLabelText("План ЭМ Щиты 2026-03")).toHaveValue(600);
  });

  it("edits, moves, copies and undoes only a proposed plan cell", async () => {
    const onMutatePlan = vi.fn().mockResolvedValue({ mutation_id: 81 });
    const onUndoPlanMutation = vi.fn().mockResolvedValue(undefined);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onMutatePlan={onMutatePlan} onUndoPlanMutation={onUndoPlanMutation} />);

    fireEvent.click(screen.getByRole("tab", { name: "Таблица ДДС" }));
    const input = screen.getByLabelText("План Щиты 2026-02");
    fireEvent.change(input, { target: { value: "450" } });
    fireEvent.blur(input);
    expect(onMutatePlan).toHaveBeenCalledWith(2, "edit", "2026-02-10", 450, 3);

    fireEvent.dragStart(screen.getByRole("button", { name: "Перенести Щиты из 2026-02" }));
    const operationRow = input.closest("tr")!;
    fireEvent.drop(operationRow.querySelectorAll("td")[2]);
    fireEvent.click(screen.getByRole("button", { name: "Переместить" }));
    expect(onMutatePlan).toHaveBeenCalledWith(2, "move", "2026-01-10", 400, 3);

    await waitFor(() => expect(screen.getByRole("button", { name: "Отменить перенос" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Отменить перенос" }));
    await waitFor(() => expect(onUndoPlanMutation).toHaveBeenCalledWith(81));
  });

  it("keeps actual rows immutable and separates currencies", () => {
    const onMutatePlan = vi.fn();
    const mixed = { ...finance, cash_flow: [
      { ...finance.cash_flow[0], actual_date: "2026-01-30", actual_amount: 1000, status: "received" },
      finance.cash_flow[1],
      { ...finance.cash_flow[1], id: 3, title: "USD invoice", currency: "USD", planned_amount: 50 },
    ] } as FinanceOverview;
    render(<DdsWorkspace finance={mixed} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onMutatePlan={onMutatePlan} />);

    expect(screen.getByLabelText("Валюта ДДС")).toHaveValue("RUB");
    fireEvent.click(screen.getByRole("tab", { name: "ДДС по месяцам" }));
    expect(screen.getByText("Факт, RUB")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Таблица ДДС" }));
    expect(screen.queryByLabelText("План Этап 1 2026-01")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Валюта ДДС"), { target: { value: "USD" } });
    expect(screen.getByLabelText("План USD invoice 2026-02")).toBeInTheDocument();
  });

  it("clamps a moved plan date to the real last day of the target month", () => {
    const onMutatePlan = vi.fn().mockResolvedValue({ mutation_id: 82 });
    const monthEnd = { ...finance, cash_flow: [
      { ...finance.cash_flow[1], planned_date: "2026-01-31" },
      { ...finance.cash_flow[0], planned_date: "2026-02-01" },
    ] } as FinanceOverview;
    render(<DdsWorkspace finance={monthEnd} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onMutatePlan={onMutatePlan} />);

    fireEvent.click(screen.getByRole("tab", { name: "Таблица ДДС" }));
    const input = screen.getByLabelText("План Щиты 2026-01");
    fireEvent.dragStart(screen.getByRole("button", { name: "Перенести Щиты из 2026-01" }));
    fireEvent.drop(input.closest("tr")!.querySelectorAll("td")[3]);
    fireEvent.click(screen.getByRole("button", { name: "Переместить" }));

    expect(onMutatePlan).toHaveBeenCalledWith(2, "move", "2026-02-28", 400, 3);
  });

  it("moves every operation in an aggregated month cell by its visible drag handle", async () => {
    const onMutatePlan = vi.fn().mockResolvedValue({ mutation_id: 91 });
    const repeatedCosts = { ...finance, cash_flow: [
      finance.cash_flow[0],
      { ...finance.cash_flow[1], title: "ЭМ Щиты", planned_date: "2026-02-10", planned_amount: 400 },
      { ...finance.cash_flow[1], id: 3, title: "эм щиты", planned_date: "2026-02-18", planned_amount: 100 },
      { ...finance.cash_flow[1], id: 4, title: "ЭМ Щиты", planned_date: "2026-03-05", planned_amount: 600 },
    ] } as FinanceOverview;
    render(<DdsWorkspace finance={repeatedCosts} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onMutatePlan={onMutatePlan} />);

    fireEvent.dragStart(screen.getByRole("button", { name: "Перенести ЭМ Щиты, 2 операций из 2026-02" }));
    const row = screen.getByText("ЭМ Щиты", { exact: true }).closest("tr")!;
    fireEvent.drop(row.querySelectorAll("td")[5]);
    fireEvent.click(screen.getByRole("button", { name: "Переместить" }));

    await waitFor(() => expect(onMutatePlan).toHaveBeenCalledTimes(2));
    expect(onMutatePlan).toHaveBeenNthCalledWith(1, 2, "move", "2026-04-10", 400, 3);
    expect(onMutatePlan).toHaveBeenNthCalledWith(2, 3, "move", "2026-04-18", 100, 3);
  });

  it("deletes a proposed plan directly from the calendar and hides it after reload", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const props = { finance, selectedContractId: 4, onPrepare: vi.fn(), onConfirm, onConfirmMany: vi.fn(), onConfirmPayment: vi.fn(), onLinkControls: vi.fn() };
    const { rerender } = render(<DdsWorkspace {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Удалить операцию Щиты" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Удалить операцию «Щиты»"));
    expect(onConfirm).toHaveBeenCalledWith("cash-flow", 2, "cancelled");
    rerender(<DdsWorkspace {...props} finance={{ ...finance, cash_flow: [finance.cash_flow[0], { ...finance.cash_flow[1], status: "cancelled" }] }} />);
    await waitFor(() => expect(screen.queryByText("Щиты", { exact: true })).not.toBeInTheDocument());
  });

  it("accepts invoice PDFs through the DDS drop zone after explicit upload", () => {
    const onDropInvoices = vi.fn().mockResolvedValue([]);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onDropInvoices={onDropInvoices} />);
    const file = new File(["invoice"], "invoice.pdf", { type: "application/pdf" });
    const zone = screen.getByText("Перетащите сюда счета PDF — один или несколько").closest(".dds-invoice-drop")!;
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    expect(onDropInvoices).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и разобрать (1)" }));
    expect(onDropInvoices).toHaveBeenCalledWith([file], expect.any(Function));
  });

  it("exports a full DDS register and the generic additional-expenses slice", () => {
    const createObjectUrl = vi.fn(() => "blob:dds");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const withExtra = { ...finance, cash_flow: [
      ...finance.cash_flow,
      { ...finance.cash_flow[1], id: 8, category: "Дополнительные расходы", title: "Непредвиденные работы" },
    ] } as FinanceOverview;
    render(<DdsWorkspace finance={withExtra} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Полный ДДС" }));
    fireEvent.click(screen.getByRole("button", { name: "Дополнительные расходы" }));
    expect(createObjectUrl).toHaveBeenCalledTimes(2);
    expect(click).toHaveBeenCalledTimes(2);
    click.mockRestore();
  });
});
