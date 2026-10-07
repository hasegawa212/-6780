import { randomUUID } from "node:crypto";
import {
  type ActiveSession,
  type AppError,
  ApplyProviderEventUseCase,
  type AuthDeps,
  type CallRecord,
  type Contact,
  type ContactCursor,
  CreateCallUseCase,
  type Deps,
  hasRole,
  isContactable,
  LoginUseCase,
  LogoutUseCase,
  RecordOutcomeUseCase,
  ResolveSessionUseCase,
  type Role,
} from "@tac/application";
import { CALL_STATUSES } from "@tac/domain";
import { MOCK_SIGNATURE_HEADER, normalizeMockStatus, verifyMockWebhook } from "@tac/telephony";
import { type Context, Hono } from "hono";
import { bodyLimit } from "hono/body-limit";
import { deleteCookie, getCookie, setCookie } from "hono/cookie";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { z } from "zod";
import { maskE164, safeEqual } from "./security.js";

/**
 * HTTP API（Phase 3・7）。業務ロジックはユースケースに置き、ここは入出力の検証・認可・応答の形だけ。
 * - organization_id・担当者はセッションから取る（本文の値は使わない。未知の項目は 400）
 * - エラーは `{ error: { code, message, requestId } }`。スタックトレース・SQL は返さない
 */

export interface ApiOptions {
  readonly deps: Deps;
  readonly auth: AuthDeps;
  /** production / staging では true（Cookie に Secure、HSTS） */
  readonly cookieSecure: boolean;
  /** 要求元の IP（ログイン試行の制限に使う）。プロキシの後ろでは信頼できるヘッダーだけから取る */
  readonly clientIp?: (c: Context) => string | undefined;
  readonly mockWebhooks: {
    /** local / test で mock のときだけ true（config.telephony.mockWebhooksEnabled） */
    readonly enabled: boolean;
    readonly secret: string | undefined;
    readonly verifySignatures: boolean;
  };
}

type Env = { Variables: { requestId: string; session: ActiveSession } };

export const SESSION_COOKIE = "tac_session";
const MAX_BODY_BYTES = 64 * 1024;
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: string,
    message: string,
    readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "メールアドレスまたはパスワードが正しくありません",
  LOGIN_LOCKED: "ログインの失敗が続いたため、しばらくログインできません",
  UNAUTHENTICATED: "ログインが必要です",
  FORBIDDEN: "この操作の権限がありません",
  CSRF_TOKEN_INVALID: "CSRF トークンがありません、または正しくありません",
  NOT_FOUND: "見つかりません",
  VALIDATION_FAILED: "入力が正しくありません",
  INVALID_JSON: "JSON として読めません",
  UNSUPPORTED_MEDIA_TYPE: "Content-Type は application/json にしてください",
  PAYLOAD_TOO_LARGE: "本文が大きすぎます",
  IDEMPOTENCY_KEY_REQUIRED: "Idempotency-Key ヘッダーが必要です",
  IDEMPOTENCY_KEY_REUSED: "同じ Idempotency-Key が別の内容で使われています",
  PROVIDER_ERROR: "電話プロバイダが発信を受け付けませんでした（自動では掛け直しません）",
  PROVIDER_TIMEOUT: "電話プロバイダの応答がありません。同じ Idempotency-Key で再送してください",
  PROVIDER_UNCERTAIN:
    "電話プロバイダとの通信が途中で切れ、発信されたか確認できません。同じ Idempotency-Key で再送してください",
  WEBHOOK_SIGNATURE_INVALID: "Webhook の署名を検証できません",
  INTERNAL: "サーバーで問題が起きました",
};
const messageOf = (code: string) => MESSAGES[code] ?? "要求を処理できませんでした";

const fail = (status: ContentfulStatusCode, code: string, extra: Record<string, unknown> = {}) =>
  new ApiError(status, code, messageOf(code), extra);

/** ユースケースのエラーコード → HTTP の状態コード */
function fromAppError(e: AppError, statuses: Record<string, ContentfulStatusCode>): ApiError {
  return fail(statuses[e.code] ?? 422, e.code, e.reasons ? { reasons: e.reasons } : {});
}

async function readJson(c: Context): Promise<unknown> {
  const type = c.req.header("content-type") ?? "";
  if (!/^application\/json(;|$)/i.test(type)) throw fail(415, "UNSUPPORTED_MEDIA_TYPE");
  const text = await c.req.text();
  try {
    return JSON.parse(text);
  } catch {
    throw fail(400, "INVALID_JSON");
  }
}

/** 入力の検証。問題の場所（path）だけを返し、入力値そのものは返さない */
function parse<T>(schema: z.ZodType<T>, value: unknown): T {
  const r = schema.safeParse(value);
  if (!r.success) {
    throw fail(400, "VALIDATION_FAILED", {
      issues: r.error.issues.map((i) => ({ path: i.path.join("."), code: i.code })),
    });
  }
  return r.data;
}

const callView = (call: CallRecord) => ({
  id: call.id,
  status: call.status,
  contactId: call.contactId,
  campaignId: call.campaignId,
  mode: call.mode,
  to: maskE164(call.to),
  provider: call.provider ?? null,
  providerCallId: call.providerCallId ?? null,
  createdAt: call.createdAt.toISOString(),
});

const contactView = (contact: Contact) => ({
  id: contact.id,
  displayName: contact.displayName,
  phone: maskE164(contact.phone),
  timeZone: contact.timeZone ?? null,
});

const listQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(500).default(50),
  cursor: z.string().max(512).optional(),
});

// NUL は PostgreSQL の text に入らず DB エラー（500）になるので、入口で拒否する（IQA-06）
const cursorSchema = z
  .object({
    n: z
      .string()
      .max(500)
      .refine((v) => !v.includes(String.fromCharCode(0))),
    i: z.uuid(),
  })
  .strict();

const encodeCursor = (c: ContactCursor) =>
  Buffer.from(JSON.stringify({ n: c.displayName, i: c.id })).toString("base64url");

function decodeCursor(raw: string): ContactCursor {
  try {
    const parsed = cursorSchema.parse(JSON.parse(Buffer.from(raw, "base64url").toString("utf8")));
    return { displayName: parsed.n, id: parsed.i };
  } catch {
    throw fail(400, "INVALID_CURSOR");
  }
}

const loginSchema = z
  .object({
    email: z.email().max(320),
    password: z.string().min(1).max(1024),
    organizationId: z.uuid().optional(),
  })
  .strict();

const createCallSchema = z
  .object({
    contactId: z.string().min(1).max(64),
    campaignId: z.string().min(1).max(64),
    mode: z.enum(["HUMAN_DIALED", "AI_VOICE"]),
  })
  .strict();

const outcomeSchema = z
  .object({
    outcome: z.string().min(1).max(64),
    callbackAt: z.iso.datetime({ offset: true }).optional(),
    appointmentAt: z.iso.datetime({ offset: true }).optional(),
  })
  .strict();

// プロバイダは項目を足すことがあるので strict にしない（未知の項目は生データとして保存するだけ）
const mockWebhookSchema = z.object({
  eventId: z.string().min(1).max(255),
  providerCallId: z.string().min(1).max(255),
  callId: z.string().max(64).optional(),
  status: z.string().min(1).max(32),
  answeredBy: z.enum(["human", "machine"]).optional(),
  occurredAt: z.iso.datetime({ offset: true }),
});

export function createApp(opts: ApiOptions) {
  const { deps, auth } = opts;
  const app = new Hono<Env>();

  app.use("*", async (c, next) => {
    const requestId = randomUUID();
    c.set("requestId", requestId);
    await next();
    c.header("x-request-id", requestId);
    c.header("x-content-type-options", "nosniff");
    c.header("x-frame-options", "DENY");
    c.header("referrer-policy", "no-referrer");
    c.header("content-security-policy", "default-src 'none'; frame-ancestors 'none'");
    c.header("permissions-policy", "camera=(), microphone=(), geolocation=()");
    c.header("cache-control", "no-store");
    if (opts.cookieSecure) {
      c.header("strict-transport-security", "max-age=63072000; includeSubDomains");
    }
  });

  app.use(
    "*",
    bodyLimit({
      maxSize: MAX_BODY_BYTES,
      onError: () => {
        throw fail(413, "PAYLOAD_TOO_LARGE");
      },
    }),
  );

  app.onError((e, c) => {
    const requestId = c.get("requestId") ?? randomUUID();
    if (e instanceof ApiError) {
      return c.json(
        { error: { code: e.code, message: e.message, requestId, ...e.extra } },
        e.status,
      );
    }
    // 想定外の失敗。詳細はサーバーのログだけに出す（本文・Cookie・SQL のパラメータ・完全な電話番号は出さない、IQA-07）
    console.error(`[${requestId}] unhandled error`, redactForLog(e));
    return c.json({ error: { code: "INTERNAL", message: messageOf("INTERNAL"), requestId } }, 500);
  });

  app.notFound((c) =>
    c.json(
      {
        error: {
          code: "NOT_FOUND",
          message: messageOf("NOT_FOUND"),
          requestId: c.get("requestId"),
        },
      },
      404,
    ),
  );

  /** セッションを確かめ、ロールと（状態を変える要求なら）CSRF トークンを検査する */
  const requireRole = (min: Role) => async (c: Context<Env>, next: () => Promise<void>) => {
    const token = getCookie(c, SESSION_COOKIE);
    const session = token ? await new ResolveSessionUseCase(auth).execute(token) : undefined;
    if (!session) throw fail(401, "UNAUTHENTICATED");
    if (UNSAFE_METHODS.has(c.req.method)) {
      const csrf = c.req.header("x-csrf-token");
      if (!csrf || !safeEqual(auth.tokens.hash(csrf), session.csrfHash)) {
        throw fail(403, "CSRF_TOKEN_INVALID");
      }
    }
    if (!hasRole(session.role, min)) throw fail(403, "FORBIDDEN");
    c.set("session", session);
    await next();
  };

  // ---- 認証 ----

  app.post("/v1/auth/login", async (c) => {
    const body = parse(loginSchema, await readJson(c));
    const clientIp = opts.clientIp?.(c);
    const r = await new LoginUseCase(auth).execute({
      email: body.email,
      password: body.password,
      ...(body.organizationId ? { organizationId: body.organizationId } : {}),
      ...(clientIp ? { clientIp } : {}),
    });
    if (!r.ok && r.error.code === "LOGIN_LOCKED") {
      c.header("retry-after", String(r.error.retryAfterSeconds ?? 900));
      throw fail(429, "LOGIN_LOCKED");
    }
    if (!r.ok) {
      throw fromAppError(r.error, {
        INVALID_CREDENTIALS: 401,
        ORGANIZATION_REQUIRED: 400,
        ORGANIZATION_NOT_ALLOWED: 403,
      });
    }
    const s = r.value;
    setCookie(c, SESSION_COOKIE, s.sessionToken, {
      httpOnly: true,
      sameSite: "Lax",
      path: "/",
      secure: opts.cookieSecure,
      maxAge: Math.floor((s.expiresAt.getTime() - auth.clock.now().getTime()) / 1000),
    });
    return c.json({
      userId: s.userId,
      displayName: s.displayName,
      organizationId: s.organizationId,
      role: s.role,
      csrfToken: s.csrfToken,
      expiresAt: s.expiresAt.toISOString(),
    });
  });

  app.post("/v1/auth/logout", requireRole("VIEWER"), async (c) => {
    const token = getCookie(c, SESSION_COOKIE);
    if (token) await new LogoutUseCase(auth).execute(token);
    deleteCookie(c, SESSION_COOKIE, { path: "/", secure: opts.cookieSecure });
    return c.body(null, 204);
  });

  app.get("/v1/me", requireRole("VIEWER"), (c) => {
    const s = c.get("session");
    return c.json({
      userId: s.userId,
      displayName: s.displayName,
      organizationId: s.organizationId,
      role: s.role,
    });
  });

  // ---- 連絡先・キャンペーン（読み取り。検索・絞り込み・取り込みは Phase 4） ----

  app.get("/v1/contacts", requireRole("VIEWER"), async (c) => {
    const q = parse(listQuerySchema, {
      limit: c.req.query("limit"),
      cursor: c.req.query("cursor"),
    });
    const s = c.get("session");
    const after = q.cursor === undefined ? undefined : decodeCursor(q.cursor);
    const rows = await deps.contacts.list(s.organizationId, { limit: q.limit + 1, after });
    const items = rows.slice(0, q.limit);
    const last = items.at(-1);
    return c.json({
      items: items.map(contactView),
      nextCursor:
        rows.length > q.limit && last
          ? encodeCursor({ displayName: last.displayName, id: last.id })
          : null,
    });
  });

  app.get("/v1/contacts/:id", requireRole("VIEWER"), async (c) => {
    const s = c.get("session");
    const contact = await deps.contacts.get(s.organizationId, c.req.param("id") ?? "");
    if (!contact) throw fail(404, "CONTACT_NOT_FOUND");
    // 画面の表示用。判定できないときは UNKNOWN（画面は発信ボタンを出さない）。最終判定は発信時にサーバーが行う
    const check = await isContactable(deps.suppression, s.organizationId, contact.phone);
    const suppression = check.unavailable ? "UNKNOWN" : check.allowed ? "NONE" : "SUPPRESSED";
    return c.json({ contact: contactView(contact), suppression });
  });

  app.get("/v1/campaigns", requireRole("VIEWER"), async (c) => {
    const s = c.get("session");
    const items = await deps.campaigns.list(s.organizationId);
    return c.json({
      items: items.map((k) => ({ id: k.id, product: k.product, paused: k.paused })),
    });
  });

  // ---- 発信・通話 ----

  app.post("/v1/calls", requireRole("OPERATOR"), async (c) => {
    const key = c.req.header("idempotency-key");
    if (!key) throw fail(400, "IDEMPOTENCY_KEY_REQUIRED");
    const body = parse(createCallSchema, await readJson(c));
    const s = c.get("session");
    const r = await new CreateCallUseCase(deps).execute({
      organizationId: s.organizationId,
      actorId: s.userId,
      agentName: s.displayName,
      idempotencyKey: key,
      ...body,
    });
    if (!r.ok) {
      throw fromAppError(r.error, {
        INVALID_IDEMPOTENCY_KEY: 400,
        ORGANIZATION_NOT_FOUND: 404,
        CONTACT_NOT_FOUND: 404,
        CAMPAIGN_NOT_FOUND: 404,
        IDEMPOTENCY_KEY_REUSED: 409,
        PROVIDER_ERROR: 502,
        PROVIDER_TIMEOUT: 504,
        // 発信されたか分からない（画面は 5xx を「未確定」として同じ冪等キーを持ち続ける）
        PROVIDER_UNCERTAIN: 504,
      });
    }
    return c.json(
      { call: callView(r.value.call), replayed: r.value.replayed },
      r.value.replayed ? 200 : 201,
    );
  });

  app.get("/v1/calls/:id", requireRole("OPERATOR"), async (c) => {
    const s = c.get("session");
    const found = await deps.calls.get(s.organizationId, c.req.param("id") ?? "");
    if (!found) throw fail(404, "CALL_NOT_FOUND");
    return c.json({ call: callView(found) });
  });

  app.post("/v1/calls/:id/outcome", requireRole("OPERATOR"), async (c) => {
    const body = parse(outcomeSchema, await readJson(c));
    const s = c.get("session");
    const r = await new RecordOutcomeUseCase(deps).execute({
      organizationId: s.organizationId,
      actorId: s.userId,
      callId: c.req.param("id") ?? "",
      outcome: body.outcome,
      ...(body.callbackAt ? { callbackAt: new Date(body.callbackAt) } : {}),
      ...(body.appointmentAt ? { appointmentAt: new Date(body.appointmentAt) } : {}),
    });
    if (!r.ok) {
      throw fromAppError(r.error, {
        INVALID_OUTCOME: 400,
        CALL_NOT_FOUND: 404,
        CAMPAIGN_NOT_FOUND: 404,
        OUTCOME_ALREADY_RECORDED: 409,
      });
    }
    const v = r.value;
    return c.json(
      {
        outcome: { callId: v.outcome.callId, code: v.outcome.code },
        followUp: v.followUp
          ? { id: v.followUp.id, kind: v.followUp.kind, dueAt: v.followUp.dueAt.toISOString() }
          : null,
        suppressed: v.suppressed,
        nextAction: v.nextAction ?? null,
        replayed: v.replayed,
      },
      v.replayed ? 200 : 201,
    );
  });

  // ---- Webhook（署名で認証。セッション・CSRF は使わない） ----

  if (opts.mockWebhooks.enabled) {
    app.post("/v1/webhooks/mock", async (c) => {
      const raw = await c.req.text();
      if (opts.mockWebhooks.verifySignatures) {
        const { secret } = opts.mockWebhooks;
        const signature = c.req.header(MOCK_SIGNATURE_HEADER);
        if (!secret || !verifyMockWebhook(secret, raw, signature, deps.clock.now())) {
          throw fail(401, "WEBHOOK_SIGNATURE_INVALID");
        }
      }
      let json: unknown;
      try {
        json = JSON.parse(raw);
      } catch {
        throw fail(400, "INVALID_JSON");
      }
      const event = parse(mockWebhookSchema, json);
      const status = normalizeMockStatus(event.status);
      if (!status || !CALL_STATUSES.includes(status)) throw fail(400, "UNKNOWN_STATUS");
      const result = await new ApplyProviderEventUseCase(deps).execute({
        provider: "mock",
        eventId: event.eventId,
        providerCallId: event.providerCallId,
        callId: event.callId,
        status,
        occurredAt: new Date(event.occurredAt),
        payload: json,
      });
      return c.json({ result: result.kind });
    });
  }

  return app;
}

/**
 * 想定外の例外をログに出せる形にする。Drizzle 等のエラーは SQL のパラメータ（電話番号・氏名）を
 * メッセージに含むので、`params:` 以降を落とし、残った E.164 らしい番号は末尾 4 桁以外を伏せる。
 */
export function redactForLog(e: unknown): string {
  const parts: string[] = [];
  let current: unknown = e;
  for (let depth = 0; current !== undefined && current !== null && depth < 3; depth++) {
    if (current instanceof Error) {
      const code = (current as { code?: unknown }).code;
      parts.push(
        `${current.name}${typeof code === "string" ? `(${code})` : ""}: ${current.stack ?? current.message}`,
      );
      current = (current as { cause?: unknown }).cause;
    } else {
      parts.push(String(current));
      current = undefined;
    }
  }
  return parts
    .join("\ncaused by ")
    .replace(/params:[^\n]*/g, "params: [redacted]")
    .replace(/\+?\d{6,11}(\d{4})/g, "+…$1");
}
