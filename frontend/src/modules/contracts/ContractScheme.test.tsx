import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContractScheme } from "./ContractScheme";

describe("ContractScheme", () => {
  it("connects parent to child and opens linked documents", () => {
    const onConnect = vi.fn();
    const onOpenDocument = vi.fn();
    render(<ContractScheme projectId={7} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд", contract_kind: "prime_reference" },
      { id: 2, number: "СП-2", title: "Наш договор", contract_kind: "revenue_subcontract", linked_documents: [{ id: 9, name: "Договор.pdf" }] },
    ]} onConnect={onConnect} onOpenDocument={onOpenDocument} />);
    fireEvent.click(screen.getByRole("button", { name: /Связать договоры/ }));
    fireEvent.click(screen.getByRole("button", { name: /Генподряд ГП-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Наш договор СП-2/ }));
    expect(onConnect).toHaveBeenCalledWith(1, 2);
    fireEvent.click(screen.getByRole("button", { name: /Наш договор СП-2/ }));
    fireEvent.click(screen.getByRole("button", { name: /Договор.pdf/ }));
    expect(onOpenDocument).toHaveBeenCalledWith(9);
  });
});
