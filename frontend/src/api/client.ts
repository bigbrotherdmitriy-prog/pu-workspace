export class ApiError extends Error {
  constructor(message: string, public status: number | null, public requestId: string) {
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
  } catch {
    throw new ApiError(
      `Сервер недоступен. Проверьте соединение и повторите попытку. Код обращения: ${requestId}`,
      null,
      requestId,
    );
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const responseRequestId = response.headers.get("X-Request-ID") || requestId;
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    const message = detail.includes(responseRequestId)
      ? detail
      : `${detail}. Код обращения: ${responseRequestId}`;
    throw new ApiError(message, response.status, responseRequestId);
  }
  return body as T;
}
