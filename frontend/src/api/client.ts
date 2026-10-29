import { humanizeErrorText } from "../i18n/humanize";
import { reportServerReachable } from "../offline/connectivity";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number | null,
    public requestId: string,
    public code?: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function cookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

/** CSRF header for state-changing requests sent with plain fetch (cookie session). */
export function csrfHeaders(): Record<string, string> {
  const token = cookie("pu_csrf");
  return token ? { "X-CSRF-Token": token } : {};
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const requestId = newRequestId();
  const method = (options.method || "GET").toUpperCase();
  const csrfToken = cookie("pu_csrf");
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        ...(!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken
          ? { "X-CSRF-Token": csrfToken }
          : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      `Сервер недоступен. Проверьте соединение и повторите попытку. Код обращения: ${requestId}`,
      null,
      requestId,
    );
  }
  reportServerReachable();
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const responseRequestId = response.headers.get("X-Request-ID") || requestId;
    const code = typeof body.detail === "object" && body.detail !== null
      && typeof body.detail.code === "string" ? body.detail.code : undefined;
    const detailMessage = typeof body.detail === "object" && body.detail !== null
      && typeof (body.detail as { message?: unknown }).message === "string"
      ? (body.detail as { message: string }).message : undefined;
    const rawDetail = detailMessage
      ?? (typeof body.detail === "string" ? body.detail : code || `HTTP ${response.status}`);
    const detail = humanizeErrorText(rawDetail);
    const message = detail.includes(responseRequestId)
      ? detail
      : `${detail}. Код обращения: ${responseRequestId}`;
    throw new ApiError(message, response.status, responseRequestId, code, body.detail);
  }
  return body as T;
}
