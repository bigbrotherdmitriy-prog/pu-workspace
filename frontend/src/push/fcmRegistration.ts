import { api } from "../api/client";

// The Android wrapper (android/PuLauncherActivity) opens
// https://puworkspace.ru/new/#fcm=<token>. The token identifies this phone,
// not the user, so it is kept across sign-outs and re-registered after the
// next sign-in.
const STORAGE_KEY = "pu_fcm_token";
const TOKEN_PATTERN = /^[A-Za-z0-9_:-]{20,512}$/;
let pendingToken = "";

function storedToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

/** Read #fcm=<token>, remember it and strip it from the address bar. */
export function captureFcmTokenFromLocation(): string | null {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return null;
  const params = new URLSearchParams(hash);
  if (!params.has("fcm")) return null;
  const token = params.get("fcm") || "";
  params.delete("fcm");
  const rest = params.toString();
  window.history.replaceState(window.history.state, "",
    `${window.location.pathname}${window.location.search}${rest ? `#${rest}` : ""}`);
  if (!TOKEN_PATTERN.test(token)) return null;
  try {
    localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // Without storage the token is still registered for this session below.
  }
  pendingToken = token;
  return token;
}

/** Register this device for push under the signed-in user (idempotent). */
export async function registerPushDevice(): Promise<boolean> {
  const token = pendingToken || storedToken();
  if (!token) return false;
  try {
    await api("/api/notifications/devices/register", {
      method: "POST",
      body: JSON.stringify({ fcm_token: token, platform: "android" }),
    });
    return true;
  } catch {
    // Push is optional; the next start retries.
    return false;
  }
}

/** Stop pushes for the user who is signing out on this device. */
export async function revokePushDevice(): Promise<boolean> {
  const token = pendingToken || storedToken();
  if (!token) return false;
  try {
    await api("/api/notifications/devices/revoke", {
      method: "POST",
      body: JSON.stringify({ fcm_token: token }),
    });
    return true;
  } catch {
    // Sign-out must not fail because of push.
    return false;
  }
}

/** Task id from a push deep link: https://puworkspace.ru/new/?project_id=<p>&task=<id>. */
export function pushTaskIdFromLocation(): number {
  const value = Number(new URLSearchParams(window.location.search).get("task"));
  return Number.isSafeInteger(value) && value > 0 ? value : 0;
}
