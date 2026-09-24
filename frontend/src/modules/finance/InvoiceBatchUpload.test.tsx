import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvoiceBatchUpload } from "./InvoiceBatchUpload";

afterEach(cleanup);

describe("invoice batch selection", () => {
  it("selects a folder including nested PDFs, reports skipped files and reviews each result", async () => {
    const first = new File(["pdf"], "a.pdf", { type: "application/pdf" });
    const second = new File(["pdf"], "a.pdf", { type: "application/pdf" });
    Object.defineProperty(first, "webkitRelativePath", { value: "Invoices/a.pdf" });
    Object.defineProperty(second, "webkitRelativePath", { value: "Invoices/nested/a.pdf" });
    const onUpload = vi.fn().mockResolvedValue([
      { name: "Invoices/a.pdf", documentIds: [91] },
      { name: "Invoices/nested/a.pdf", documentIds: [92] },
    ]);
    const onReview = vi.fn();
    render(<InvoiceBatchUpload onUpload={onUpload} onReview={onReview} />);
    const folder = screen.getByLabelText("Выбрать папку счетов");
    expect(folder).toHaveAttribute("webkitdirectory");
    fireEvent.change(folder, { target: { files: [first, second, new File(["text"], "notes.txt")] } });
    expect(screen.getByRole("status")).toHaveTextContent("Пропущено файлов других форматов: 1");
    expect(onUpload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и разобрать (2)" }));
    expect(onUpload).toHaveBeenCalledWith([first, second], expect.any(Function));
    await screen.findByRole("list", { name: "Результаты загрузки счетов" });
    fireEvent.click(screen.getAllByRole("button", { name: "Проверить счёт" })[1]);
    await waitFor(() => expect(onReview).toHaveBeenCalledWith(92));
  });

  it("accepts more than twenty dragged invoices, deduplicates selection and prevents duplicate submissions", async () => {
    const files = Array.from({ length: 21 }, (_, i) => new File(["pdf"], `${i}.pdf`, { type: "application/pdf" }));
    let finish!: (value: []) => void;
    const onUpload = vi.fn((_files: File[], _progress: (message: string) => void) => new Promise<[]>((resolve) => { finish = resolve; }));
    render(<InvoiceBatchUpload onUpload={onUpload} />);
    const zone = screen.getByText("Перетащите сюда счета PDF — один или несколько").closest(".dds-invoice-drop")!;
    fireEvent.drop(zone, { dataTransfer: { files } });
    fireEvent.drop(zone, { dataTransfer: { files } });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и разобрать (21)" }));
    expect(onUpload.mock.calls[0][0]).toHaveLength(21);
    expect(screen.getByRole("button", { name: "Обработка счетов…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Обработка счетов…" }));
    expect(onUpload).toHaveBeenCalledTimes(1);
    finish([]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Добавить счета" })).toBeEnabled());
  });

  it("shows per-file failures alongside successful documents", async () => {
    render(<InvoiceBatchUpload onUpload={vi.fn().mockResolvedValue([
      { name: "a.pdf", documentIds: [1] }, { name: "b.pdf", documentIds: [], error: "OCR failed" },
    ])} onReview={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Выбрать файлы счетов"), { target: { files: [new File(["pdf"], "a.pdf"), new File(["pdf"], "b.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и разобрать (2)" }));
    expect(await screen.findByText(/OCR failed/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Проверить счёт" })).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Обработано: 1. С ошибками: 1");
  });

  it("rejects an oversized selection explicitly instead of truncating it", () => {
    const onUpload = vi.fn();
    render(<InvoiceBatchUpload onUpload={onUpload} />);
    fireEvent.change(screen.getByLabelText("Выбрать файлы счетов"), { target: { files: Array.from({ length: 101 }, (_, i) => new File(["pdf"], `${i}.pdf`)) } });
    expect(screen.getByRole("alert")).toHaveTextContent("до 100 счетов");
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Загрузить и разобрать (0)" })).toBeDisabled();
  });
});
