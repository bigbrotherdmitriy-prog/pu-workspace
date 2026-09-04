import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentsModule, type DocumentListItem } from "./DocumentsModule";

afterEach(cleanup);

const documents: DocumentListItem[] = [
  {
    id: 31,
    name: "Техническое задание.docx",
    summary: "Требования к срокам поставки оборудования.",
    source: "local_upload",
    status: "analyzed",
    current_version: 2,
    ocr_reprocess_available: false,
  },
  {
    id: 12,
    name: "Договор поставки.pdf",
    summary: "Основной договор с поставщиком «Булат».",
    source: "google_drive_copy",
    status: "analyzed",
    current_version: 1,
    ocr_reprocess_available: true,
  },
  {
    id: 24,
    name: "Скан без названия.pdf",
    summary: "Акт приёмки выполненных работ за август.",
    source: "google_drive_copy",
    status: "discovered",
    current_version: 1,
    ocr_reprocess_available: true,
  },
  {
    id: 7,
    name: "Авансовый отчёт.xlsx",
    summary: "Документ не удалось проанализировать.",
    source: "local_upload",
    status: "failed",
    current_version: 1,
    ocr_reprocess_available: false,
  },
];

function renderRegistry() {
  return render(
    <DocumentsModule
      collapsed={false}
      knowledgeMode={false}
      documents={documents}
      selected={null}
      onSelect={vi.fn()}
      projectId={1}
      onOcrComplete={vi.fn()}
    />,
  );
}

function visibleDocumentNames(container: HTMLElement) {
  const register = container.querySelector(".document-register");
  expect(register).not.toBeNull();
  return within(register as HTMLElement).getAllByRole("button").map((button) =>
    button.querySelector("strong")?.textContent,
  );
}

describe("DocumentsModule smart registry", () => {
  it("searches case-insensitively by both document name and summary", () => {
    renderRegistry();
    const search = screen.getByPlaceholderText("Поиск по названию или сводке");

    fireEvent.change(search, { target: { value: "ДОГОВОР" } });
    expect(screen.getByText("Договор поставки.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Техническое задание.docx")).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "акт приёмки" } });
    expect(screen.getByText("Скан без названия.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Договор поставки.pdf")).not.toBeInTheDocument();
  });

  it("combines status and source filters", () => {
    renderRegistry();

    fireEvent.change(screen.getByRole("combobox", { name: "Статус" }), { target: { value: "analyzed" } });
    expect(screen.getByText("Техническое задание.docx")).toBeInTheDocument();
    expect(screen.getByText("Договор поставки.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Скан без названия.pdf")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Источник" }), { target: { value: "google_drive_copy" } });
    expect(screen.getByText("Договор поставки.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Техническое задание.docx")).not.toBeInTheDocument();
  });

  it("sorts the visible register by document name", () => {
    const { container } = renderRegistry();

    fireEvent.change(screen.getByRole("combobox", { name: "Сортировка" }), { target: { value: "name_asc" } });

    expect(visibleDocumentNames(container)).toEqual([
      "Авансовый отчёт.xlsx",
      "Договор поставки.pdf",
      "Скан без названия.pdf",
      "Техническое задание.docx",
    ]);
  });

  it("shows a contextual empty state and resets all registry controls", () => {
    renderRegistry();
    const search = screen.getByPlaceholderText("Поиск по названию или сводке");

    fireEvent.change(search, { target: { value: "несуществующий документ" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Статус" }), { target: { value: "failed" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Источник" }), { target: { value: "local_upload" } });
    const emptyMessage = screen.getByText("По заданным условиям документы не найдены");
    expect(emptyMessage).toBeInTheDocument();

    fireEvent.click(within(emptyMessage.parentElement as HTMLElement).getByRole("button", { name: "Сбросить фильтры" }));
    expect(search).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Статус" })).toHaveValue("all");
    expect(screen.getByRole("combobox", { name: "Источник" })).toHaveValue("all");
    for (const item of documents) expect(screen.getByText(item.name)).toBeInTheDocument();
  });

  it("supports quick views for processed documents and documents needing attention", () => {
    renderRegistry();

    fireEvent.click(screen.getByRole("button", { name: /^Обработаны/ }));
    expect(screen.getByText("Техническое задание.docx")).toBeInTheDocument();
    expect(screen.getByText("Договор поставки.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Скан без названия.pdf")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Требуют внимания/ }));
    expect(screen.queryByText("Договор поставки.pdf")).not.toBeInTheDocument();
    expect(screen.queryByText("Скан без названия.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("Авансовый отчёт.xlsx")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Все/ }));
    for (const item of documents) expect(screen.getByText(item.name)).toBeInTheDocument();
  });
});
