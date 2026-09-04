import { afterEach, describe, expect, it, vi } from "vitest";
import { requestContractDeletionConfirmation } from "./contractDeletion";

describe("requestContractDeletionConfirmation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("prefills the exact contract number required by the API", () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("ГК-08-194/25");

    expect(requestContractDeletionConfirmation("ГК-08-194/25")).toBe("ГК-08-194/25");
    expect(prompt).toHaveBeenCalledWith(
      expect.stringContaining("Исходные документы не удаляются"),
      "ГК-08-194/25",
    );
  });

  it("keeps cancellation explicit", () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    expect(requestContractDeletionConfirmation("Д-1")).toBeNull();
  });
});
