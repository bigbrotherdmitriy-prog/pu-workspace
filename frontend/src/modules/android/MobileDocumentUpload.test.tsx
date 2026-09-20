import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { MobileDocumentUpload } from "./MobileDocumentUpload";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
afterEach(cleanup);
beforeEach(() => { vi.mocked(api).mockReset(); });

function setup(projectId = 7) {
  const onClose = vi.fn(), onComplete = vi.fn();
  const view = render(<MobileDocumentUpload open projectId={projectId} onClose={onClose} onComplete={onComplete} />);
  const input = view.container.querySelector('input[webkitdirectory]') as HTMLInputElement;
  return { input, onClose, onComplete };
}

describe("local document upload", () => {
  it("shows one unambiguous folder action for the project-folder flow", () => {
    render(<MobileDocumentUpload open projectId={7} folderOnly title="Разобрать папку проекта" onClose={vi.fn()} onComplete={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Выбрать папку проекта/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Выбрать файлы/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сфотографировать/ })).not.toBeInTheDocument();
    expect(screen.getByText(/До подтверждения ничего не будет загружено/)).toBeInTheDocument();
  });

  it("keeps nested paths and uses the selected project only after explicit submit", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ status: "queued", processed: 0, tasks: 0, risks: 0, skipped: [], jobs: [{ job_id: 41, status: "queued" }] })
      .mockResolvedValueOnce({
        job_id: 41, status: "completed", progress: 100, error: null,
        result: { processed: 1, skipped: 0, tasks: 0, risks: 1, decisions: 0, drafts: 0, documents: [19] },
      });
    const { input, onComplete, onClose } = setup();
    const file = new File(["synthetic"], "sample.txt", { type: "text/plain" });
    Object.defineProperty(file, "webkitRelativePath", { value: "QA/nested/sample.txt" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText("QA/nested/sample.txt")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" }));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    const [path, options] = vi.mocked(api).mock.calls[0];
    expect(path).toBe("/local-upload/analyze");
    expect(JSON.parse(options!.body as string)).toMatchObject({ project_id: 7, files: [{ path: "QA/nested/sample.txt", mime_type: "text/plain" }] });
    expect(api).toHaveBeenNthCalledWith(2, "/local-upload/projects/7/jobs/41");
    expect(onComplete).toHaveBeenCalledWith(expect.stringContaining("Обработано: 1"), [19]);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("accepts dropped files through the shared drop zone", () => {
    setup();
    const dropZone = screen.getByLabelText("Перетащите файлы сюда");
    fireEvent.drop(dropZone, { dataTransfer: { files: [new File(["pdf"], "invoice.pdf", { type: "application/pdf" })] } });
    expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
  });

  it("skips unsupported folder artifacts without blocking supported invoices", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ status: "queued", jobs: [{ job_id: 61, status: "queued" }] })
      .mockResolvedValueOnce({
        job_id: 61, status: "completed", progress: 100, error: null,
        result: { processed: 1, skipped: 0, tasks: 0, risks: 0, decisions: 0, drafts: 0, documents: [29] },
      });
    const { input, onComplete } = setup();
    const invoice = new File(["pdf"], "invoice.pdf", { type: "application/pdf" });
    const systemFile = new File(["system"], "desktop.ini", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [invoice, systemFile] } });

    expect(screen.getByText(/Пропущено неподдерживаемых файлов: 1 \(desktop.ini\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить desktop.ini" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" }));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    const request = JSON.parse(vi.mocked(api).mock.calls[0][1]!.body as string);
    expect(request.files).toHaveLength(1);
    expect(request.files[0]).toMatchObject({ path: "invoice.pdf", mime_type: "application/pdf" });
  });

  it("explains when a folder contains no supported documents", () => {
    const { input } = setup();
    fireEvent.change(input, { target: { files: [new File(["system"], "desktop.ini")] } });
    expect(screen.getByText(/Пропущено неподдерживаемых файлов: 1 \(desktop.ini\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить и проанализировать (0)" })).toBeDisabled();
  });

  it("rejects more than 50 files instead of silently truncating", () => {
    const { input } = setup();
    fireEvent.change(input, { target: { files: Array.from({ length: 51 }, (_, i) => new File(["x"], `${i}.txt`)) } });
    expect(screen.getByText(/Выбрано больше 50 файлов/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить и проанализировать (0)" })).toBeDisabled();
    expect(api).not.toHaveBeenCalled();
  });

  it("does not upload without a project", () => {
    const { input } = setup(0);
    fireEvent.change(input, { target: { files: [new File(["x"], "sample.txt")] } });
    expect(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" })).toBeDisabled();
  });

  it("disables changes while uploading and retains files on failure", async () => {
    let reject!: (reason: Error) => void;
    vi.mocked(api).mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    const { input, onClose } = setup();
    fireEvent.change(input, { target: { files: [new File(["x"], "sample.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" }));
    await waitFor(() => expect(api).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Закрыть" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Удалить sample.txt" })).toBeDisabled();
    await act(async () => reject(new Error("Synthetic failure")));
    expect(await screen.findByText("Synthetic failure")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" })).toBeEnabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows a durable job failure and does not report completion", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ status: "queued", jobs: [{ job_id: 52, status: "queued" }] })
      .mockResolvedValueOnce({ job_id: 52, status: "failed", progress: 10, error: "JobError", result: null });
    const { input, onComplete, onClose } = setup();
    fireEvent.change(input, { target: { files: [new File(["x"], "invoice.pdf", { type: "application/pdf" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить и проанализировать (1)" }));
    expect(await screen.findByText(/JobError/)).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
