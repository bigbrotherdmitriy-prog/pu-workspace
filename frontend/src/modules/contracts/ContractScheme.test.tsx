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

  it("accepts application files on the selected contract", () => {
    const onDropApplications = vi.fn();
    render(<ContractScheme projectId={9} contracts={[
      { id: 3, number: "Д-3", title: "Договор", contract_kind: "customer" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropApplications={onDropApplications} />);
    fireEvent.click(screen.getByRole("button", { name: /Заказчик Д-3/ }));
    const file = new File(["application"], "Приложение №1.txt", { type: "text/plain" });
    const applicationLabels = screen.getAllByText("Приложения к этому договору");
    fireEvent.drop(applicationLabels[applicationLabels.length - 1].closest("label")!, {
      dataTransfer: { files: [file] },
    });
    expect(onDropApplications).toHaveBeenCalledWith([file], 3);
  });

  it("routes dropped GPR, budget and DDS files through the selected contract", () => {
    const onDropFinance = vi.fn();
    const { container } = render(<ContractScheme projectId={10} contracts={[
      { id: 4, number: "СП-4", title: "Субподряд", contract_kind: "downstream_subcontract" },
    ]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFinance={onDropFinance} />);
    fireEvent.click(container.querySelector(".contract-node-open")!);
    const file = new File(["work;date\nЭтап;2026-09-01"], "ГПР.csv", { type: "text/csv" });
    fireEvent.drop(container.querySelector(".contract-finance-drops label")!, { dataTransfer: { files: [file] } });
    expect(onDropFinance).toHaveBeenCalledWith([file], 4, "schedule");
  });

  it("accepts a photographed contract from the file picker", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={11} contracts={[]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    const input = container.querySelector<HTMLInputElement>('.contract-scheme-file-drop input[type="file"]')!;
    expect(input.accept).toContain(".jpg");
    const photo = new File(["photo"], "Договор-фото.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [photo] } });
    expect(onDropFiles).toHaveBeenCalledWith([photo], undefined);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("Получено файлов: 1");
  });

  it("accepts a contract dropped anywhere on the constructor", () => {
    const onDropFiles = vi.fn();
    const { container } = render(<ContractScheme projectId={12} contracts={[]} onConnect={vi.fn()} onOpenDocument={vi.fn()} onDropFiles={onDropFiles} />);
    const pdf = new File(["contract"], "Договор.pdf", { type: "application/pdf" });
    fireEvent.drop(container.querySelector(".contract-scheme")!, { dataTransfer: { files: [pdf], types: ["Files"], getData: () => "" } });
    expect(onDropFiles).toHaveBeenCalledWith([pdf], undefined);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("Передаю на загрузку и анализ");
  });
});
