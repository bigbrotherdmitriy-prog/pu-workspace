import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContractsModule } from "./ContractsModule";

afterEach(cleanup);

function renderModule(overrides: Partial<ComponentProps<typeof ContractsModule>> = {}) {
  const noop = vi.fn();
  const onCreate = vi.fn();
  render(<ContractsModule
    collapsed={false}
    number="Д-15"
    title="Монтаж"
    counterparty="Подрядчик"
    kind="customer"
    parentContractId={0}
    amount="100000"
    advanceAmount="0"
    retentionPercent="5"
    signedAt="2026-08-31"
    contracts={[]}
    onNumberChange={noop}
    onTitleChange={noop}
    onCounterpartyChange={noop}
    onKindChange={noop}
    onParentContractIdChange={noop}
    onAmountChange={noop}
    onAdvanceAmountChange={noop}
    onRetentionPercentChange={noop}
    onSignedAtChange={noop}
    onCreate={onCreate}
    {...overrides}
  ><div>Каталог документов</div></ContractsModule>);
  return onCreate;
}

describe("ContractsModule", () => {
  it("keeps manual creation collapsed while exposing the document catalog", () => {
    renderModule();
    expect(screen.getByText("Создать договор").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByLabelText("Номер договора")).not.toBeVisible();
    expect(screen.getByText("Каталог документов")).toBeVisible();
  });

  it("opens the existing fields and creates a valid customer contract", () => {
    const onCreate = renderModule();
    fireEvent.click(screen.getByText("Создать договор"));
    expect(screen.getByText("Создать договор").closest("details")).toHaveAttribute("open");
    expect(screen.getByLabelText("Номер договора")).toBeVisible();
    expect(screen.getByLabelText("Номер договора")).toHaveValue("Д-15");
    expect(screen.getByLabelText("Название договора")).toHaveValue("Монтаж");
    expect(screen.getByLabelText("Контрагент")).toHaveValue("Подрядчик");
    expect(screen.getByLabelText("Вид договора")).toHaveValue("customer");
    expect(screen.getByLabelText("Сумма договора, ₽")).toHaveValue(100000);
    expect(screen.getByLabelText("Аванс, ₽")).toHaveValue(0);
    expect(screen.getByLabelText("Удержание, %")).toHaveValue(5);
    expect(screen.getByLabelText("Дата подписания договора")).toHaveValue("2026-08-31");
    fireEvent.click(screen.getByRole("button", { name: "Добавить договор" }));
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it.each([{ number: " " }, { title: " " }, { kind: "revenue_subcontract" as const }])("preserves required-field validation for %j", (overrides) => {
    const onCreate = renderModule(overrides);
    fireEvent.click(screen.getByText("Создать договор"));
    const createButton = screen.getByRole("button", { name: "Добавить договор" });
    expect(createButton).toBeDisabled();
    fireEvent.click(createButton);
    expect(onCreate).not.toHaveBeenCalled();
  });
});
