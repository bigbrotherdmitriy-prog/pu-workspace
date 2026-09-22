import { describe, expect, it } from "vitest";

import { isSupportedLocalUploadFile, localUploadMimeType, localUploadProgressMessage } from "./localUploadJobs";

describe("local upload MIME normalization", () => {
  it.each(["", "application/octet-stream", "application/msword"])("accepts legacy DOC with browser MIME %s", (type) => {
    const file = new File(["synthetic doc"], "Договор.DOC", { type });
    expect(localUploadMimeType(file)).toBe("application/msword");
    expect(isSupportedLocalUploadFile(file)).toBe(true);
  });

  it("recognizes a PDF or scan even when the browser omits its MIME type", () => {
    expect(localUploadMimeType(new File(["pdf"], "invoice.PDF"))).toBe("application/pdf");
    expect(localUploadMimeType(new File(["image"], "scan.JPEG"))).toBe("image/jpeg");
  });

  it("uses a supported extension when the browser reports a generic MIME type", () => {
    const invoice = new File(["pdf"], "invoice.pdf", { type: "application/octet-stream" });
    expect(localUploadMimeType(invoice)).toBe("application/pdf");
    expect(isSupportedLocalUploadFile(invoice)).toBe(true);
    expect(isSupportedLocalUploadFile(new File(["system"], "desktop.ini"))).toBe(false);
  });

  it("shows durable job identity and real progress during a long OCR run", () => {
    expect(localUploadProgressMessage({ job_id: 7580, status: "running", progress: 10 })).toBe(
      "OCR и анализ выполняются: файл 1 из 1, 10% · задача №7580",
    );
    expect(localUploadProgressMessage({ job_id: 7580, status: "completed", progress: 100 })).toBe(
      "OCR завершён: файл 1 из 1, 100% · задача №7580",
    );
  });
});
