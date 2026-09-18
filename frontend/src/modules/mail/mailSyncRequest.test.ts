import { expect, it } from "vitest";
import { mailSyncRequest } from "./mailSyncRequest";

it("requests sent mail independently of the recent mixed-mail window", () => {
  expect(mailSyncRequest("sent")).toEqual({ query: "in:sent", max_results: 25 });
});
it("preserves the bounded background sync without a selected folder", () => {
  expect(mailSyncRequest()).toEqual({ query: "newer_than:7d", max_results: 25 });
});
it("explicitly refreshes trash and spam, rather than the inbox", () => {
  expect(mailSyncRequest("trash").query).toBe("in:trash");
  expect(mailSyncRequest("spam").query).toBe("in:spam");
});
