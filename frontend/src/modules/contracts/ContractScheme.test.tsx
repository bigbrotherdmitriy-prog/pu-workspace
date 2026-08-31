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

  it("separates contracts saved at the same coordinates", () => {
    window.localStorage.setItem("pu-contract-scheme:8", JSON.stringify({ 1: { x: 30, y: 30 }, 2: { x: 30, y: 30 } }));
    const { container } = render(<ContractScheme projectId={8} contracts={[
      { id: 1, number: "ГП-1", title: "Генподряд", contract_kind: "prime_reference" },
      { id: 2, number: "СП-2", title: "Исполнитель", contract_kind: "downstream_subcontract", parent_contract_id: 1 },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} />);
    const nodes = Array.from(container.querySelectorAll<HTMLElement>(".contract-scheme-node"));
    expect(`${nodes[0].style.left}:${nodes[0].style.top}`).not.toBe(`${nodes[1].style.left}:${nodes[1].style.top}`);
    expect(screen.getByText("← ГП-1")).toBeInTheDocument();
  });
});
