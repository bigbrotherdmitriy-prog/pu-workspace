import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DdsMobileMonth, defaultDdsMonth, type DdsMobileRow } from "./DdsMobileMonth";

afterEach(cleanup);
const row = (id: number, direction: string, planned_date: string, planned_amount: number, category: string, extra: Partial<DdsMobileRow> = {}): DdsMobileRow =>
  ({ id, direction, planned_date, planned_amount, category, status: "approved", object: "Общие", ...extra });
const rows = [
  row(1, "inflow", "2026-09-05", 1_000_000, "Аванс заказчика"),
  row(2, "outflow", "2026-09-10", 250_000, "Материалы"),
  row(3, "outflow", "2026-09-20", 150_000, "Материалы"),
  row(4, "outflow", "2026-09-25", 90_000, "Субподряд"),
  row(5, "outflow", "2026-10-02", 400_000, "Субподряд"),
  row(6, "outflow", "2026-09-30", 999_999, "Материалы", { status: "cancelled" }),
];
const text = (value: string) => value.replace(/\s/g, " ");

describe("DdsMobileMonth", () => {
  it("lists categories with month sums and totals, without cancelled rows", () => {
    render(<DdsMobileMonth rows={rows} currency="RUB" today="2026-09" />);
    expect(screen.getByLabelText("Месяц ДДС")).toHaveValue("2026-09");
    const outflow = screen.getByRole("region", { name: "Затраты" });
    const items = within(outflow).getAllByRole("listitem").map((item) => text(item.textContent || ""));
    expect(items).toEqual(["Материалы2 операций400 000,00 ₽", "Субподряд90 000,00 ₽"]);
    const totals = screen.getByLabelText(/Итоги за/);
    expect(text(totals.textContent || "")).toContain("Приход1 000 000,00 ₽");
    expect(text(totals.textContent || "")).toContain("Расход490 000,00 ₽");
    expect(text(totals.textContent || "")).toContain("Поток за месяц510 000,00 ₽");
  });

  it("switches the month and carries the balance over", () => {
    render(<DdsMobileMonth rows={rows} currency="RUB" today="2026-09" />);
    fireEvent.change(screen.getByLabelText("Месяц ДДС"), { target: { value: "2026-10" } });
    const totals = text(screen.getByLabelText(/Итоги за/).textContent || "");
    expect(totals).toContain("Расход400 000,00 ₽");
    expect(totals).toContain("Остаток на конец месяца110 000,00 ₽");
    expect(within(screen.getByRole("region", { name: "Поступления" })).getByText("Нет операций")).toBeInTheDocument();
  });

  it("labels foreign currency and names the object when there are several", () => {
    render(<DdsMobileMonth rows={[row(1, "outflow", "2026-09-05", 10, "Оборудование", { object: "Корпус 1" }), row(2, "outflow", "2026-09-06", 5, "Оборудование", { object: "Корпус 2" })]} currency="EUR" today="2026-09" />);
    expect(screen.getByText("Корпус 1")).toBeInTheDocument();
    expect(text(screen.getByLabelText(/Итоги за/).textContent || "")).toContain("15,00 EUR");
  });

  it("explains an empty plan", () => {
    render(<DdsMobileMonth rows={[]} currency="RUB" today="2026-09" />);
    expect(screen.getByText("Добавьте первую плановую операцию ДДС.")).toBeInTheDocument();
  });

  it("opens on the current month, else the next one with operations, else the last", () => {
    expect(defaultDdsMonth(["2026-09", "2026-10", "2026-12"], "2026-10")).toBe("2026-10");
    expect(defaultDdsMonth(["2026-09", "2026-12"], "2026-10")).toBe("2026-12");
    expect(defaultDdsMonth(["2026-08", "2026-09"], "2026-10")).toBe("2026-09");
    expect(defaultDdsMonth([], "2026-10")).toBe("");
  });
});
