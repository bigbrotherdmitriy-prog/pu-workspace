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
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} />);

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
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={onConfirmMany} onConfirmPayment={vi.fn()} />);

    fireEvent.click(screen.getByRole("tab", { name: "Детализация" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать операцию Щиты" }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить выбранные (1)" }));

    expect(onConfirmMany).toHaveBeenCalledWith("cash-flow", [2], "approved");
  });

  it("exports every active workbook view as an Excel-compatible CSV", () => {
    const createObjectUrl = vi.fn(() => "blob:dds");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<DdsWorkspace finance={finance} selectedContractId={4} onPrepare={vi.fn()} onConfirm={vi.fn()} onConfirmMany={vi.fn()} onConfirmPayment={vi.fn()} />);

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
