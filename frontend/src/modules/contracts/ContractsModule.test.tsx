import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContractsModule } from "./ContractsModule";

function renderModule(onCreate = vi.fn()) {
  const noop = vi.fn();
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
  ><div>Каталог документов</div></ContractsModule>);
  return onCreate;
}

describe("ContractsModule", () => {
  it("creates a valid customer contract and exposes its document catalog", () => {
    const onCreate = renderModule();
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
    expect(onCreate).toHaveBeenCalledOnce();
    expect(screen.getByText("Каталог документов")).toBeInTheDocument();
    expect(screen.getByLabelText("Дата подписания договора")).toHaveValue("2026-08-31");
  });
});
