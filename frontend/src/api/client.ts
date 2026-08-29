export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem("pu_token");
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body as T;
}
