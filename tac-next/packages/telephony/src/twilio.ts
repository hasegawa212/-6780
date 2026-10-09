import { createHmac, timingSafeEqual } from "node:crypto";
import {
  type CreateProviderCallRequest,
  type ProviderCall,
  type ProviderCallCandidate,
  type ProviderCallQuery,
  ProviderRejectedError,
  ProviderTimeoutError,
  type TelephonyProvider,
  type TransferTarget,
} from "@tac/application";
import type { CallStatus, E164 } from "@tac/domain";

/**
 * Twilio の Voice アダプタ（Phase 11、ADR-0015）。
 *
 * API の形は Twilio 公式の OpenAPI（twilio-oai `twilio_api_v2010.json`）と公式 SDK（twilio-node 6.1.2）で確認した。
 * 発信は現行 TAC（`telegram-ai-bot/tac/outbound.py`）と同じ「会議（Conference）でつなぐ」方式。
 * ただし順序を変え、**先に担当者、次にお客様**へ発信する：担当者のレッグで失敗したら、お客様には一度も発信しない。
 *
 * Twilio の発信 API には冪等キーが無い。だからこのアダプタは**自分で再送しない**。
 * 結果が分からない失敗（接続断・5xx・タイムアウト）は ProviderRejectedError 以外の例外で返し、
 * 通話は REQUESTED のまま Webhook で確定させる（IQA-03）。
 */

export const TWILIO_WEBHOOK_PATH = "/v1/webhooks/twilio";
const API_BASE = "https://api.twilio.com";
/** Twilio の `Twiml` 引数の上限（OpenAPI の説明: Max 4000 characters） */
const MAX_TWIML_LENGTH = 4000;
/** Call の SID（実物は CA＋英数字 32 桁）。英数字だけに限ってパスの書き換えを防ぐ */
const SID = /^CA[0-9A-Za-z]{1,64}$/;

export interface TwilioOptions {
  readonly accountSid: string;
  readonly authToken: string;
  /** 担当者の電話番号（会議を始める側）。発信元はキャンペーンの番号（campaign.callerId） */
  readonly agentNumber: E164;
  /** Twilio から届く Webhook の公開 URL のオリジン（例 https://tac-next.fly.dev）。署名の検証にも使う */
  readonly publicBaseUrl: string;
  /** 呼び出しを待つ秒数（Twilio の Timeout） */
  readonly ringTimeoutSeconds: number;
  /** 1 通話の最長秒数（Twilio の TimeLimit。担当者が会議に1人で残り続けない） */
  readonly timeLimitSeconds: number;
  /** Twilio への 1 要求の待ち時間（既定 10 秒） */
  readonly requestTimeoutMs?: number;
  readonly fetch?: typeof fetch;
}

/** Twilio が発信を受け付けなかった（4xx）。お客様のレッグは作られていない */
export class TwilioRejectedError extends ProviderRejectedError {
  constructor(message: string) {
    super(message);
    this.name = "TwilioRejectedError";
  }
}

/** 発信されたかどうか分からない失敗（接続断・5xx・応答の形が想定外） */
export class TwilioUncertainError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TwilioUncertainError";
  }
}

const RAW_TO_STATUS: Readonly<Record<string, CallStatus>> = {
  queued: "DIALING",
  // StatusCallbackEvent=initiated の通知（OpenAPI の call_enum_event）。発信の直後と同じ扱い
  initiated: "DIALING",
  ringing: "RINGING",
  "in-progress": "IN_PROGRESS",
  completed: "ENDED",
  busy: "BUSY",
  failed: "FAILED",
  "no-answer": "NO_ANSWER",
  canceled: "CANCELED",
};

/** Twilio の CallStatus をドメインの状態にする。知らない値は undefined（推測で状態を作らない） */
export function normalizeTwilioStatus(raw: string): CallStatus | undefined {
  return Object.hasOwn(RAW_TO_STATUS, raw) ? RAW_TO_STATUS[raw] : undefined;
}

// ---- Webhook の署名（X-Twilio-Signature） ----

export const TWILIO_SIGNATURE_HEADER = "x-twilio-signature";

type Params = Readonly<Record<string, string | readonly string[]>>;

/**
 * 公式 SDK の getExpectedTwilioSignature と同じ計算：URL の後ろに、キーの昇順で「キー＋値」を連結し、
 * 認証トークンで HMAC-SHA1 → base64。配列の値は重複を除いて昇順に並べる。
 */
export function twilioSignature(authToken: string, url: string, params: Params): string {
  const data = Object.keys(params)
    .sort()
    .reduce((acc, key) => {
      const value = params[key];
      if (typeof value === "string") return acc + key + value;
      return acc + [...new Set(value)].sort().reduce((a, v) => a + key + v, "");
    }, url);
  return createHmac("sha1", authToken).update(Buffer.from(data, "utf-8")).digest("base64");
}

/** 署名を定数時間で比べる。URL は Host ヘッダーから組み立てず、設定した公開 URL を使うこと */
export function verifyTwilioSignature(
  authToken: string,
  url: string,
  params: Params,
  header: string | undefined,
): boolean {
  if (!header) return false;
  const expected = Buffer.from(twilioSignature(authToken, url, params));
  const actual = Buffer.from(header);
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

// ---- TwiML ----

const escapeXml = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");

/**
 * 会議に入る TwiML。担当者（agent）が入ると会議が始まり、どちらかが切ると会議が終わる。
 * お客様（customer）は、出た直後に名乗りを聞き、担当者が入るまで待つ。録音はしない（RECORDING は既定 OFF、ADR-0010）。
 */
export function conferenceTwiml(room: string, role: "agent" | "customer", disclosure = ""): string {
  const say =
    role === "customer" && disclosure ? `<Say language="ja-JP">${escapeXml(disclosure)}</Say>` : "";
  const start = role === "agent" ? "true" : "false";
  return (
    '<?xml version="1.0" encoding="UTF-8"?>' +
    `<Response>${say}<Dial><Conference startConferenceOnEnter="${start}" ` +
    `endConferenceOnExit="true" beep="false">${escapeXml(room)}</Conference></Dial></Response>`
  );
}

/** お客様のレッグの状態通知の URL（通話 ID を載せる。URL ごと署名されるので改ざんできない） */
export function twilioStatusCallbackUrl(publicBaseUrl: string, callId: string): string {
  return `${publicBaseUrl.replace(/\/+$/, "")}${TWILIO_WEBHOOK_PATH}?callId=${encodeURIComponent(callId)}`;
}

// ---- アダプタ ----

type Outcome =
  | { readonly kind: "ok"; readonly body: unknown }
  | { readonly kind: "rejected"; readonly status: number; readonly code: string }
  | { readonly kind: "uncertain"; readonly reason: string }
  | { readonly kind: "timeout" };

export class TwilioTelephonyProvider implements TelephonyProvider {
  readonly name = "twilio";
  private readonly fetch: typeof fetch;
  private readonly timeoutMs: number;
  private readonly authorization: string;
  /** 同じプロセス内で、同じ冪等キーの発信を 2 回 Twilio に送らない */
  private readonly inFlight = new Map<string, Promise<ProviderCall>>();

  constructor(private readonly opts: TwilioOptions) {
    if (!/^https:\/\//.test(opts.publicBaseUrl)) {
      throw new Error("Twilio の publicBaseUrl は https:// で始めてください");
    }
    this.fetch = opts.fetch ?? fetch;
    this.timeoutMs = opts.requestTimeoutMs ?? 10_000;
    this.authorization = `Basic ${Buffer.from(`${opts.accountSid}:${opts.authToken}`).toString("base64")}`;
  }

  createCall(request: CreateProviderCallRequest): Promise<ProviderCall> {
    const known = this.inFlight.get(request.idempotencyKey);
    if (known) return known;
    const placing = this.place(request);
    this.inFlight.set(request.idempotencyKey, placing);
    // 確実に失敗した（お客様に発信していない）ときだけ忘れる。結果が分からないものは同じ結果を返し続ける
    placing.catch((e: unknown) => {
      if (e instanceof ProviderRejectedError) this.inFlight.delete(request.idempotencyKey);
    });
    return placing;
  }

  private async place(request: CreateProviderCallRequest): Promise<ProviderCall> {
    const room = `tac-${request.callId}`;
    const customerTwiml = conferenceTwiml(room, "customer", request.disclosureText);
    if (customerTwiml.length > MAX_TWIML_LENGTH) {
      // 名乗りを切り詰めて発信しない（名乗りが欠けた勧誘になる）
      throw new TwilioRejectedError("disclosure is too long for Twilio's Twiml parameter");
    }
    const limit = String(this.opts.timeLimitSeconds);

    // 1) 担当者。ここで失敗しても、お客様には発信していないので「受け付けられなかった」扱い
    const agent = await this.post(this.callsUrl(), [
      ["To", this.opts.agentNumber],
      ["From", request.from],
      ["Twiml", conferenceTwiml(room, "agent")],
      ["Timeout", String(this.opts.ringTimeoutSeconds)],
      ["TimeLimit", limit],
    ]);
    if (agent.kind !== "ok") {
      throw new TwilioRejectedError(`operator leg was not placed (${describe(agent)})`);
    }
    const agentSid = sidOf(agent.body);
    if (!agentSid) throw new TwilioRejectedError("operator leg returned no call SID");

    // 2) お客様。再送はしない（Twilio に冪等キーが無い）
    const customer = await this.post(this.callsUrl(), [
      ["To", request.to],
      ["From", request.from],
      ["Twiml", customerTwiml],
      ["StatusCallback", twilioStatusCallbackUrl(this.opts.publicBaseUrl, request.callId)],
      ["StatusCallbackMethod", "POST"],
      ["StatusCallbackEvent", "initiated"],
      ["StatusCallbackEvent", "ringing"],
      ["StatusCallbackEvent", "answered"],
      ["StatusCallbackEvent", "completed"],
      ["Timeout", String(this.opts.ringTimeoutSeconds)],
      ["TimeLimit", limit],
    ]);
    if (customer.kind === "rejected") {
      // 担当者を会議に1人で待たせない
      await this.hangUp(agentSid, ["canceled", "completed"]).catch(() => undefined);
      throw new TwilioRejectedError(`customer leg was rejected (${describe(customer)})`);
    }
    if (customer.kind === "timeout") throw new ProviderTimeoutError();
    if (customer.kind === "uncertain") {
      throw new TwilioUncertainError(`customer leg outcome unknown (${customer.reason})`);
    }
    const sid = sidOf(customer.body);
    const status = normalizeTwilioStatus(statusOf(customer.body) ?? "queued");
    if (!sid || !status) {
      throw new TwilioUncertainError("customer leg response had no call SID or a known status");
    }
    return { provider: this.name, providerCallId: sid, status };
  }

  async getCall(providerCallId: string): Promise<ProviderCall> {
    const r = await this.send("GET", this.callUrl(providerCallId));
    if (r.kind !== "ok") throw new TwilioUncertainError(`fetch call failed (${describe(r)})`);
    const raw = statusOf(r.body) ?? "";
    const status = normalizeTwilioStatus(raw);
    if (!status) throw new TwilioUncertainError("Twilio returned an unknown call status");
    return { provider: this.name, providerCallId, status };
  }

  /**
   * 通話一覧（`GET …/Calls.json?To=&From=&PageSize=50`、OpenAPI の ListCallResponse）から、REST で発信した通話を返す。
   * 続きのページがあれば、全部を見ていないので失敗にする（見落としで「発信されなかった」と決めない、ADR-0016）。
   */
  async findCalls(query: ProviderCallQuery): Promise<readonly ProviderCallCandidate[]> {
    const params = new URLSearchParams({ To: query.to, From: query.from, PageSize: "50" });
    const r = await this.send("GET", `${this.callsUrl()}?${params.toString()}`);
    if (r.kind !== "ok") throw new TwilioUncertainError(`list calls failed (${describe(r)})`);
    const body = r.body as { calls?: unknown; next_page_uri?: unknown };
    if (!Array.isArray(body.calls))
      throw new TwilioUncertainError("list calls returned no calls array");
    if (typeof body.next_page_uri === "string" && body.next_page_uri !== "") {
      throw new TwilioUncertainError("list calls has another page; not every call was seen");
    }
    const found: ProviderCallCandidate[] = [];
    for (const c of body.calls) {
      const sid = sidOf(c);
      const createdAt = Date.parse(field(c, "date_created") ?? "");
      if (!sid || field(c, "direction") !== "outbound-api" || Number.isNaN(createdAt)) continue;
      if (createdAt < query.createdAfter.getTime()) continue;
      found.push({
        providerCallId: sid,
        status: normalizeTwilioStatus(statusOf(c) ?? ""),
        createdAt: new Date(createdAt),
      });
    }
    return found;
  }

  /** つながっている通話は completed、まだ鳴っている通話は canceled で終わる（OpenAPI の call_enum_update_status） */
  async endCall(providerCallId: string): Promise<void> {
    await this.hangUp(providerCallId, ["completed", "canceled"]);
  }

  async transferCall(_providerCallId: string, _target: TransferTarget): Promise<void> {
    throw new Error("transfer is not implemented for Twilio yet (Phase 13)");
  }

  private async hangUp(sid: string, order: readonly ("completed" | "canceled")[]): Promise<void> {
    const url = this.callUrl(sid);
    let last: Outcome | undefined;
    for (const status of order) {
      last = await this.post(url, [["Status", status]]);
      if (last.kind === "ok") return;
      if (last.kind !== "rejected") break;
    }
    throw new TwilioUncertainError(`hang up failed (${last ? describe(last) : "no attempt"})`);
  }

  private callsUrl(): string {
    return `${API_BASE}/2010-04-01/Accounts/${encodeURIComponent(this.opts.accountSid)}/Calls.json`;
  }

  private callUrl(sid: string): string {
    if (!SID.test(sid)) throw new Error("not a Twilio call SID");
    return `${API_BASE}/2010-04-01/Accounts/${encodeURIComponent(this.opts.accountSid)}/Calls/${sid}.json`;
  }

  private post(url: string, form: readonly (readonly [string, string])[]): Promise<Outcome> {
    const body = new URLSearchParams();
    for (const [k, v] of form) body.append(k, v);
    return this.send("POST", url, body.toString());
  }

  private async send(method: "GET" | "POST", url: string, body?: string): Promise<Outcome> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await this.fetch(url, {
        method,
        headers: {
          authorization: this.authorization,
          accept: "application/json",
          ...(body === undefined ? {} : { "content-type": "application/x-www-form-urlencoded" }),
        },
        ...(body === undefined ? {} : { body }),
        signal: controller.signal,
      });
    } catch (e) {
      if (controller.signal.aborted) return { kind: "timeout" };
      return { kind: "uncertain", reason: e instanceof Error ? e.name : "network error" };
    } finally {
      clearTimeout(timer);
    }
    const text = await response.text().catch(() => "");
    if (response.status >= 400 && response.status < 500) {
      // Twilio のエラー本文は電話番号を含むことがあるので、コードだけを残す
      return { kind: "rejected", status: response.status, code: errorCodeOf(text) };
    }
    if (!response.ok) return { kind: "uncertain", reason: `HTTP ${response.status}` };
    try {
      return { kind: "ok", body: text === "" ? {} : JSON.parse(text) };
    } catch {
      return { kind: "uncertain", reason: "response was not JSON" };
    }
  }
}

function describe(o: Outcome): string {
  switch (o.kind) {
    case "rejected":
      return `HTTP ${o.status}, Twilio error ${o.code}`;
    case "uncertain":
      return o.reason;
    case "timeout":
      return "timeout";
    default:
      return "ok";
  }
}

const field = (body: unknown, key: string): string | undefined => {
  if (typeof body !== "object" || body === null) return undefined;
  const v = (body as Record<string, unknown>)[key];
  return typeof v === "string" && v !== "" ? v : undefined;
};
const sidOf = (body: unknown) => {
  const sid = field(body, "sid");
  return sid && SID.test(sid) ? sid : undefined;
};
const statusOf = (body: unknown) => field(body, "status");

function errorCodeOf(text: string): string {
  try {
    const code = (JSON.parse(text) as { code?: unknown }).code;
    return typeof code === "number" || (typeof code === "string" && /^\d{1,6}$/.test(code))
      ? String(code)
      : "unknown";
  } catch {
    return "unknown";
  }
}
