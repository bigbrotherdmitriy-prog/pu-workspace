import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DdsWorkspace } from "./DdsWorkspace";
import type { FinanceOverview } from "./types";

const finance = {
  cash_flow: [
    { id: 1, contract_id: 4, direction: "inflow", title: "Этап 1", planned_date: "2026-01-29", planned_amount: 1000, actual_amount: 0, currency: "RUB", record_version: 1, object_name: "Дубна", category: "Приход от заказчика", note: "Оплата этапа", status: "approved" },
    { id: 2, contract_id: 4, direction: "outflow", title: "Щиты", planned_date: "2026-02-10", planned_amount: 400, actual_amount: 0, currency: "RUB", record_version: 3, object_name: "Общие", category: "Оборудование", note: "Аванс", status: "proposed" },
  ],
} as FinanceOverview;

afterEach(cleanup);

describe("DdsWorkspace", () => {
  it("shows every workbook view and recalculates summaries from detail rows", () => {
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    expect(screen.getByText("январь 2026 г.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    expect(screen.getByText("Оплата этапа")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Сводка" }));
    expect(screen.getByText("По объектам")).toBeInTheDocument();
    expect(screen.getByText("Расходы по статьям")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Календарь (вид ГПР)" }));
    expect(screen.getByText("ДУБНА")).toBeInTheDocument();
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

  it("exports every active workbook view as an Excel-compatible CSV", () => {
    const createObjectUrl = vi.fn(() => "blob:dds");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Экспорт: ДДС по месяцам" }));
    fireEvent.click(screen.getByRole("tab", { name: "Календарь (вид ГПР)" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Календарь (вид ГПР)" }));
    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Детализация" }));
    fireEvent.click(screen.getByRole("tab", { name: "Сводка" }));
    fireEvent.click(screen.getByRole("button", { name: "Экспорт: Сводка" }));

    expect(createObjectUrl).toHaveBeenCalledTimes(4);
    expect(click).toHaveBeenCalledTimes(4);
    expect(revokeObjectUrl).toHaveBeenCalledTimes(4);
    click.mockRestore();
  });

  it("edits, moves, copies and undoes only a proposed plan cell", async () => {
    const onMutatePlan = vi.fn().mockResolvedValue({ mutation_id: 81 });
    const onUndoPlanMutation = vi.fn().mockResolvedValue(undefined);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onMutatePlan={onMutatePlan} onUndoPlanMutation={onUndoPlanMutation} />);

    fireEvent.click(screen.getByRole("tab", { name: "Календарь (вид ГПР)" }));
    const input = screen.getByLabelText("План Щиты 2026-02");
    fireEvent.change(input, { target: { value: "450" } });
    fireEvent.blur(input);
    expect(onMutatePlan).toHaveBeenCalledWith(2, "edit", "2026-02-10", 450, 3);

    fireEvent.dragStart(input.parentElement!);
    const operationRow = input.closest("tr")!;
    fireEvent.drop(operationRow.querySelectorAll("td")[2]);
    fireEvent.click(screen.getByRole("button", { name: "Переместить" }));
    expect(onMutatePlan).toHaveBeenCalledWith(2, "move", "2026-01-10", 400, 3);

    await waitFor(() => expect(screen.getByRole("button", { name: "Отменить" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
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
    expect(screen.getByText("Факт, RUB")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Календарь (вид ГПР)" }));
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

    fireEvent.click(screen.getByRole("tab", { name: "Календарь (вид ГПР)" }));
    const input = screen.getByLabelText("План Щиты 2026-01");
    fireEvent.dragStart(input.parentElement!);
    fireEvent.drop(input.closest("tr")!.querySelectorAll("td")[3]);
    fireEvent.click(screen.getByRole("button", { name: "Переместить" }));

    expect(onMutatePlan).toHaveBeenCalledWith(2, "move", "2026-02-28", 400, 3);
  });

  it("accepts invoice PDFs through the DDS drop zone", () => {
    const onDropInvoices = vi.fn();
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} onLinkControls={vi.fn()} onDropInvoices={onDropInvoices} />);
    const file = new File(["invoice"], "invoice.pdf", { type: "application/pdf" });
    const zone = screen.getByText("Перетащите сюда счёт PDF").closest(".dds-invoice-drop")!;
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    expect(onDropInvoices).toHaveBeenCalledWith([file]);
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
