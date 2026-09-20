import { describe, expect, it } from "vitest";

import { isSupportedLocalUploadFile, localUploadMimeType } from "./localUploadJobs";

describe("local upload MIME normalization", () => {
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
});
