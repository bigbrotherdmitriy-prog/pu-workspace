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
  it("keeps nested paths and uses the selected project only after explicit submit", async () => {
    vi.mocked(api).mockResolvedValue({ processed: 1, tasks: 0, risks: 1, skipped: [] });
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
    expect(onClose).toHaveBeenCalledOnce();
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
});
