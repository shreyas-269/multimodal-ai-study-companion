import { getFirebaseAuth } from "./firebase";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryAfterS?: number;

  constructor(code: string, message: string, status: number, retryAfterS?: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryAfterS = retryAfterS;
  }
}

interface ErrorPayload {
  code: string;
  message: string;
  retry_after_s?: number;
}

function isErrorEnvelope(data: unknown): data is { error: ErrorPayload } {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  const envelope = data as Record<string, unknown>;
  if (typeof envelope.error !== "object" || envelope.error === null) {
    return false;
  }
  const err = envelope.error as Record<string, unknown>;
  return typeof err.code === "string" && typeof err.message === "string";
}

export async function apiFetch<T>(
  path: string,
  options?: {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    signal?: AbortSignal;
    auth?: boolean;
  }
): Promise<T> {
  const baseUrlEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrlEnv) {
    throw new Error("Missing required environment variable: NEXT_PUBLIC_API_BASE_URL");
  }

  if (!path.startsWith("/")) {
    throw new Error(`apiFetch path must start with '/': got '${path}'`);
  }

  const baseUrl = baseUrlEnv.replace(/\/+$/, "");
  const url = `${baseUrl}${path}`;

  const headers: Record<string, string> = { ...(options?.headers ?? {}) };

  if (options?.auth !== false && typeof window !== "undefined") {
    const auth = getFirebaseAuth();
    await auth.authStateReady();
    if (auth.currentUser) {
      const token = await auth.currentUser.getIdToken();
      headers["Authorization"] = `Bearer ${token}`;
    }
  }

  let requestBody: BodyInit | undefined;
  if (options?.body instanceof FormData) {
    requestBody = options.body;
  } else if (options?.body !== undefined) {
    if (!headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    requestBody = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: options?.method ?? (options?.body ? "POST" : "GET"),
      headers,
      body: requestBody,
      signal: options?.signal,
    });
  } catch {
    throw new ApiError("network_error", "Could not reach the backend", 0);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (response.ok) {
    return (await response.json()) as T;
  }

  let errorData: unknown = null;
  try {
    errorData = await response.json();
  } catch {
    // Response body is not valid JSON (e.g. HTML 502, empty body)
  }

  if (isErrorEnvelope(errorData)) {
    const retryAfterS =
      typeof errorData.error.retry_after_s === "number"
        ? errorData.error.retry_after_s
        : undefined;
    throw new ApiError(errorData.error.code, errorData.error.message, response.status, retryAfterS);
  }

  throw new ApiError("http_error", `HTTP ${response.status}`, response.status);
}
