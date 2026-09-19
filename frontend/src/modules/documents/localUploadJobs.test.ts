import { describe, expect, it } from "vitest";

import { localUploadMimeType } from "./localUploadJobs";

describe("local upload MIME normalization", () => {
  it("recognizes a PDF or scan even when the browser omits its MIME type", () => {
    expect(localUploadMimeType(new File(["pdf"], "invoice.PDF"))).toBe("application/pdf");
    expect(localUploadMimeType(new File(["image"], "scan.JPEG"))).toBe("image/jpeg");
  });
});
