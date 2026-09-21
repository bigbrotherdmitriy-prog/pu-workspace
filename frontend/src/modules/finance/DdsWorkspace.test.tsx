import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DdsWorkspace } from "./DdsWorkspace";
import type { FinanceOverview } from "./types";

const finance = {
  cash_flow: [
    { id: 1, contract_id: 4, direction: "inflow", title: "Этап 1", planned_date: "2026-01-29", planned_amount: 1000, actual_amount: 0, object_name: "Дубна", category: "Приход от заказчика", note: "Оплата этапа", status: "approved" },
    { id: 2, contract_id: 4, direction: "outflow", title: "Щиты", planned_date: "2026-02-10", planned_amount: 400, actual_amount: 0, object_name: "Общие", category: "Оборудование", note: "Аванс", status: "proposed" },
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
});
