import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { awaitLocalUploadJobs } from "../documents/localUploadJobs";
import { uploadInvoiceBatch } from "./invoiceUploadTransport";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
vi.mock("../documents/localUploadJobs", () => ({ awaitLocalUploadJobs: vi.fn(), localUploadMimeType: () => "application/pdf" }));
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(awaitLocalUploadJobs).mockReset(); });

describe("invoice upload transport", () => {
  it("uploads all 21 files individually, preserves nested paths and continues after failure", async () => {
    const files = Array.from({ length: 21 }, (_, i) => new File(["test"], `${i}.pdf`));
    Object.defineProperty(files[0], "webkitRelativePath", { value: "invoices/nested/0.pdf" });
    let calls = 0;
    vi.mocked(api).mockImplementation(async () => {
      calls += 1;
      if (calls === 2) throw new Error("upload failed");
      return { jobs: [{ job_id: calls, status: "queued" }] };
    });
    vi.mocked(awaitLocalUploadJobs).mockImplementation(async (_project, jobs) => ({ processed: 1, skipped: 0, tasks: 0, risks: 0, decisions: 0, drafts: 0, documents: [jobs[0].job_id + 100] }));
    const result = await uploadInvoiceBatch(17, files, vi.fn());
    expect(api).toHaveBeenCalledTimes(21);
    expect(result).toHaveLength(21);
    expect(result[1].error).toBe("upload failed");
    expect(result[20].documentIds).toEqual([121]);
    const payload = JSON.parse(vi.mocked(api).mock.calls[0][1]!.body as string);
    expect(payload.project_id).toBe(17);
    expect(payload.files).toHaveLength(1);
    expect(payload.files[0].path).toBe("invoices/nested/0.pdf");
    expect(api).toHaveBeenCalledWith("/local-upload/analyze", expect.any(Object));
    expect(vi.mocked(api).mock.calls.every(([path]) => path === "/local-upload/analyze")).toBe(true);
  });
});
