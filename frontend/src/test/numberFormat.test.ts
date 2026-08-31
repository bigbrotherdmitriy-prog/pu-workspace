import { describe, expect, it } from "vitest";
import { formatMoney, formatNumber } from "../utils/numberFormat";

describe("number formatting", () => {
  it("separates thousands and always renders two decimal places", () => {
    expect(formatNumber(1234567890.5).replace(/\u00a0|\u202f/g, " ")).toBe("1 234 567 890,50");
  });

  it("formats monetary values consistently", () => {
    expect(formatMoney("125 400,5").replace(/\u00a0|\u202f/g, " ")).toBe("125 400,50 ₽");
  });

  it("uses a safe zero for missing and invalid values", () => {
    expect(formatNumber(undefined)).toBe("0,00");
    expect(formatNumber("not-a-number")).toBe("0,00");
  });
});
