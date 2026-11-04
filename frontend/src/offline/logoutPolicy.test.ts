import { expect, it } from "vitest";
import { logoutPurgePolicy, logoutPurgeWarning } from "./logoutPolicy";
import { clearOfflineData } from "./db";
it("uses global privacy purge by default and explicit actor scope", () => {
  expect(logoutPurgePolicy("")).toBe("all_local_data");
  expect(logoutPurgePolicy("current_actor")).toBe("current_actor");
  expect(() => logoutPurgePolicy("typo")).toThrow();
  expect(logoutPurgeWarning("all_local_data")).toContain("всех пользователей и проектов");
  expect(logoutPurgeWarning("current_actor")).toContain("вашей учётной записи во всех проектах");
});
it("rejects invalid purge actors before opening IDB", async () => {
  await expect(clearOfflineData({scope:"current_actor",userId:0})).rejects.toThrow("actor");
  await expect(clearOfflineData({scope:"current_actor",userId:NaN})).rejects.toThrow("actor");
});
