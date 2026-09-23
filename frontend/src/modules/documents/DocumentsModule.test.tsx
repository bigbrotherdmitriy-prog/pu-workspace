import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentsModule, type DocumentCard } from "./DocumentsModule";

afterEach(cleanup);

function document(source: string, available: boolean, reason?: string): DocumentCard {
  return {
    id: 7, name: "synthetic.pdf", source, status: "analyzed", current_version: 1,
    mime_type: "application/pdf", versions: [{ version: 1, created_at: "2026-09-03" }],
    links: { tasks: 0, risks: 0, decisions: 0, drafts: 0 },
    summary: "Синтетическое описание документа", ocr_reprocess_available: available, ocr_reprocess_unavailable_reason: reason,
  };
}

describe("DocumentsModule OCR availability", () => {
  it("shows description and source as explicit document fields", () => {
    const selected = document("google_drive_copy", true);
    render(<DocumentsModule collapsed={false} knowledgeMode={false} documents={[selected]} selected={selected} onSelect={vi.fn()} projectId={1} onOcrComplete={vi.fn()} />);
    const information = screen.getByLabelText(`Сведения о документе ${selected.name}`);
    expect(information).toHaveTextContent("ОписаниеСинтетическое описание документа");
    expect(information).toHaveTextContent("ИсточникРабочая копия Google Диска");
    expect(information).toHaveTextContent("СтатусПроанализирован");
    expect(information).toHaveTextContent("Версия1");
    expect(information).toHaveTextContent("ТипPDF-документ");
  });

  it("explains when a document description is not available yet", () => {
    const selected = { ...document("local_upload", true), summary: undefined };
    render(<DocumentsModule collapsed={false} knowledgeMode={false} documents={[selected]} selected={selected} onSelect={vi.fn()} projectId={1} onOcrComplete={vi.fn()} />);
    expect(screen.getByText("Описание появится после анализа содержимого.")).toBeInTheDocument();
  });

  it("sends an empty project to folder analysis instead of presenting the document register as an upload dump", () => {
    const openProjectFolder = vi.fn();
    const addImportantDocument = vi.fn();
    render(<DocumentsModule collapsed={false} knowledgeMode={false} documents={[]} selected={null} onSelect={vi.fn()} projectId={1} onOcrComplete={vi.fn()} onOpenProjectFolder={openProjectFolder} onAddImportantDocument={addImportantDocument} />);
    expect(screen.getByText("Важных документов пока нет")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать папку проекта" }));
    expect(openProjectFolder).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Добавить один документ" }));
    expect(addImportantDocument).toHaveBeenCalledOnce();
  });

  it("does not offer impossible re-OCR for local uploads and explains recovery", () => {
    const selected = document("local_upload", false, "original_not_available");
    render(<DocumentsModule collapsed={false} knowledgeMode={false} documents={[selected]} selected={selected} onSelect={vi.fn()} projectId={1} onOcrComplete={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Повторно распознать этот документ" })).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Загрузите файл ещё раз");
    expect(screen.getByRole("button", { name: "Повторно распознать доступные сканы" })).toBeDisabled();
  });

  it("keeps re-OCR available when the provider original can be reloaded", () => {
    const selected = document("google_drive_copy", true);
    render(<DocumentsModule collapsed={false} knowledgeMode={false} documents={[selected]} selected={selected} onSelect={vi.fn()} projectId={1} onOcrComplete={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Повторно распознать этот документ" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Повторно распознать доступные сканы" })).toBeEnabled();
  });
});
