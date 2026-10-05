import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ContractDetails } from "./ContractDetails";
import { inheritedVatLabel, vatLabel } from "./contractCommercial";

afterEach(cleanup);

describe("contract commercial display", () => {
  it("opens immutable historical values without offering restore or changing the current card", () => {
    render(<ContractDetails contract={{ id: 41, record_version: 3, number: "SYN-1", title: "Synthetic", amount: "731.23",
      version_history: [{ id: 51, sequence: 2, event: "updated", changed_fields: ["amount"], occurred_at: "2026-09-30T10:00:00Z",
        snapshot: { amount: "112.34", advance_amount: "0.00", vat_mode: "rate", vat_rate: "0.00" } }] }}><span>Linked package</span></ContractDetails>);
    fireEvent.click(screen.getByText("История договора"));
    fireEvent.click(screen.getByText("Значения версии 2"));
    expect(screen.getByText("112,34 ₽")).toBeVisible();
    expect(screen.getByText("731,23 ₽")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Восстановить/ })).not.toBeInTheDocument();
  });

  it("renders exact decimal money, zero conditions and separate dates in one labeled card", () => {
    const { container } = render(<ContractDetails contract={{ id: 41, record_version: 3, number: "SYN-1", title: "Synthetic",
      amount: "9999999999999999.99", advance_amount: "0.00", retention_percent: "0", vat_mode: "rate", vat_rate: "0.00",
      performed_to: "2026-12-31", warranty_until: "2027-12-31" }}><button>Explicit budget action</button></ContractDetails>);
    expect(screen.getByRole("article", { name: "Карточка договора SYN-1" })).toBeInTheDocument();
    expect(container.querySelector(".contract-commercial-summary")?.textContent).toContain("9\u00a0999\u00a0999\u00a0999\u00a0999\u00a0999,99 ₽");
    expect(container.querySelector(".contract-commercial-summary")).toHaveTextContent("Аванс с НДС0,00 ₽");
    expect(container.querySelector(".contract-commercial-summary")).toHaveTextContent("Удержание0%");
    expect(screen.getByText("НДС 0%")).toBeInTheDocument();
    expect(screen.getByText("31.12.2026")).toBeInTheDocument();
    expect(screen.getByText("31.12.2027")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Explicit budget action" })).toBeInTheDocument();
  });

  it("uses the project currency for exact current and historical monetary values", () => {
    render(<ContractDetails currency="USD" contract={{ id: 41, record_version: 3, number: "SYN-USD", title: "Synthetic",
      amount: "731.23", advance_amount: "112.34", version_history: [{ id: 52, sequence: 2, event: "updated",
        changed_fields: ["amount"], occurred_at: "2026-09-30T10:00:00Z", snapshot: { amount: "71.23" } }] }}><span>Package</span></ContractDetails>);
    expect(screen.getByText("731,23 USD")).toBeInTheDocument();
    expect(screen.getByText("112,34 USD")).toBeInTheDocument();
    fireEvent.click(screen.getByText("История договора"));
    fireEvent.click(screen.getByText("Значения версии 2"));
    expect(screen.getByText("71,23 USD")).toBeVisible();
    expect(screen.queryByText(/₽/)).not.toBeInTheDocument();
  });

  it("distinguishes unspecified, without VAT and zero rate without deriving tax from retention", () => {
    expect(vatLabel("unspecified")).toBe("НДС не указан");
    expect(vatLabel("none")).toBe("Без НДС");
    expect(vatLabel("rate", "0.00")).toBe("НДС 0%");
    expect(vatLabel("rate", "22.20")).toBe("НДС 22.2%");
    expect(inheritedVatLabel(null)).toContain("происхождение условий не зафиксировано");
    expect(inheritedVatLabel({ schema_version: 1, mode: "none", rate: null, source_contract_id: 41, source_contract_record_version: 3 }))
      .toBe("Без НДС · условия договора №41, v3");
  });
});
