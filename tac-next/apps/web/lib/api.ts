"use client";

/**
 * API クライアント（同じオリジンの /v1/*。Cookie セッションはブラウザが送る）。
 * CSRF トークンはログイン時に受け取り、このタブの sessionStorage に置く（認証情報そのものではない。
 * セッションは HttpOnly Cookie で、JS からは読めない）。
 */

const CSRF_KEY = "tac.csrf";

export class ApiError extends Error {
  constructor(
    /** HTTP の状態コード。通信そのものが失敗したら undefined */
    readonly status: number | undefined,
    readonly code: string | undefined,
    readonly reasons: readonly string[] = [],
  ) {
    super(code ?? "NETWORK_ERROR");
  }
}

export function rememberCsrf(token: string): void {
  try {
    sessionStorage.setItem(CSRF_KEY, token);
  } catch {
    // ストレージが使えなくても、状態を変える要求が 403 になるだけ（安全側）
  }
}

export function forgetCsrf(): void {
  try {
    sessionStorage.removeItem(CSRF_KEY);
  } catch {}
}

function csrf(): string | undefined {
  try {
    return sessionStorage.getItem(CSRF_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export async function api<T>(
  method: "GET" | "POST",
  path: string,
  opts: { body?: unknown; headers?: Record<string, string> } = {},
): Promise<{ status: number; data: T }> {
  const headers: Record<string, string> = { ...opts.headers };
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  const token = csrf();
  if (method !== "GET" && token) headers["x-csrf-token"] = token;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      ...(opts.body === undefined ? {} : { body: JSON.stringify(opts.body) }),
    });
  } catch {
    throw new ApiError(undefined, undefined);
  }
  const data = res.status === 204 ? undefined : await res.json().catch(() => undefined);
  if (!res.ok) {
    const error = (data as { error?: { code?: string; reasons?: string[] } } | undefined)?.error;
    throw new ApiError(res.status, error?.code, error?.reasons ?? []);
  }
  return { status: res.status, data: data as T };
}

export interface Me {
  userId: string;
  displayName: string;
  organizationId: string;
  role: string;
}

export interface ContactView {
  id: string;
  displayName: string;
  phone: string;
  timeZone: string | null;
}

export interface CallView {
  id: string;
  status: import("@tac/domain").CallStatus;
  mode: import("@tac/domain").CallMode;
  contactId: string;
  campaignId: string;
  to: string;
}
