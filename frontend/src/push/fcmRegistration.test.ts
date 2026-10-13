import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TOKEN = "fcm-synthetic-token:APA91b" + "z".repeat(40);

async function freshModule() {
  vi.resetModules();
  return import("./fcmRegistration");
}

function okFetch() {
  const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => new Response("{}",
    { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/new/");
});
afterEach(() => vi.unstubAllGlobals());

describe("FCM token hand-over from the Android wrapper", () => {
  it("captures #fcm= and strips it while keeping path and query", async () => {
    const { captureFcmTokenFromLocation } = await freshModule();
    window.history.replaceState(null, "", `/new/?project_id=17&section=notifications#fcm=${encodeURIComponent(TOKEN)}`);
    expect(captureFcmTokenFromLocation()).toBe(TOKEN);
    expect(window.location.hash).toBe("");
    expect(`${window.location.pathname}${window.location.search}`).toBe("/new/?project_id=17&section=notifications");
    expect(localStorage.getItem("pu_fcm_token")).toBe(TOKEN);
  });

  it("strips a malformed token without storing it", async () => {
    const { captureFcmTokenFromLocation } = await freshModule();
    window.history.replaceState(null, "", "/new/#fcm=bad%20token");
    expect(captureFcmTokenFromLocation()).toBeNull();
    expect(window.location.hash).toBe("");
    expect(localStorage.getItem("pu_fcm_token")).toBeNull();
  });

  it("leaves unrelated fragments alone", async () => {
    const { captureFcmTokenFromLocation } = await freshModule();
    window.history.replaceState(null, "", "/new/#section-anchor");
    expect(captureFcmTokenFromLocation()).toBeNull();
    expect(window.location.hash).toBe("#section-anchor");
  });

  it("registers the captured token and revokes it on sign-out", async () => {
    const { captureFcmTokenFromLocation, registerPushDevice, revokePushDevice } = await freshModule();
    window.history.replaceState(null, "", `/new/#fcm=${TOKEN}`);
    captureFcmTokenFromLocation();
    const fetchMock = okFetch();
    expect(await registerPushDevice()).toBe(true);
    expect(await revokePushDevice()).toBe(true);
    expect(fetchMock.mock.calls.map(([path, init]) => [path, init?.method, JSON.parse(String(init?.body))])).toEqual([
      ["/api/notifications/devices/register", "POST", { fcm_token: TOKEN, platform: "android" }],
      ["/api/notifications/devices/revoke", "POST", { fcm_token: TOKEN }],
    ]);
    // The phone keeps its token for the next user who signs in.
    expect(localStorage.getItem("pu_fcm_token")).toBe(TOKEN);
  });

  it("re-registers a remembered token after a later start without the fragment", async () => {
    localStorage.setItem("pu_fcm_token", TOKEN);
    const { registerPushDevice } = await freshModule();
    const fetchMock = okFetch();
    expect(await registerPushDevice()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does nothing in a browser without a token and never throws on API errors", async () => {
    const { registerPushDevice, revokePushDevice } = await freshModule();
    const fetchMock = okFetch();
    expect(await registerPushDevice()).toBe(false);
    expect(await revokePushDevice()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    localStorage.setItem("pu_fcm_token", TOKEN);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    expect(await registerPushDevice()).toBe(false);
    expect(await revokePushDevice()).toBe(false);
  });
});

describe("push deep link to a task", () => {
  it("reads a positive integer task id only", async () => {
    const { pushTaskIdFromLocation } = await freshModule();
    window.history.replaceState(null, "", "/new/?project_id=17&task=42");
    expect(pushTaskIdFromLocation()).toBe(42);
    for (const bad of ["?task=abc", "?task=-3", "?task=1.5", "?section=tasks"]) {
      window.history.replaceState(null, "", `/new/${bad}`);
      expect(pushTaskIdFromLocation()).toBe(0);
    }
  });
});
