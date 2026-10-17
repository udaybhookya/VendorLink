export const VENDOR_URL = import.meta.env.VITE_VENDOR_URL ?? "http://localhost:8001";
export const RETAILER_URL = import.meta.env.VITE_RETAILER_URL ?? "http://localhost:8002";
export const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public detail: string,
  ) {
    super(`${status} ${code}${detail ? `: ${detail}` : ""}`);
  }
}

export async function api<T>(base: string, path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const response = await fetch(base + path, {
    ...rest,
    headers: { ...(json !== undefined ? { "content-type": "application/json" } : {}), ...rest.headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    throw new ApiError(response.status, detail?.error ?? "error", detail?.detail ?? (typeof detail === "string" ? detail : ""));
  }
  return data as T;
}

/** Subscribe to a Server-Sent Events stream; returns an unsubscribe function. */
export function subscribe(url: string, onEvent: (type: string, data: unknown) => void): () => void {
  const source = new EventSource(url);
  const handler = (e: MessageEvent) => {
    try {
      onEvent(e.type, JSON.parse(e.data));
    } catch {
      onEvent(e.type, e.data);
    }
  };
  for (const type of ["deal", "rules", "listing", "document", "status", "question", "order", "queue", "vendor", "config", "audit", "security_event", "task", "job", "info_response"]) {
    source.addEventListener(type, handler);
  }
  return () => source.close();
}

export const shortHash = (h?: string | null) => (h ? `${h.slice(0, 8)}…` : "–");
