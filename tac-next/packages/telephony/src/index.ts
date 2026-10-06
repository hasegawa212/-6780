import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type {
  CreateProviderCallRequest,
  ProviderCall,
  TelephonyProvider,
  TransferTarget,
} from "@tac/application";
import type { CallStatus } from "@tac/domain";

/** シミュレーターのシナリオ（VOICE.md「テスト用の電話シミュレーター」） */
export type SimulatedScenario =
  | "ANSWER"
  | "BUSY"
  | "REJECT"
  | "NO_ANSWER"
  | "DISCONNECT"
  | "VOICEMAIL"
  | "PROVIDER_ERROR";

/** mock プロバイダの状態の語彙（実在のプロバイダと同じく、ドメインの CallStatus とは別の文字列） */
export type MockRawStatus =
  | "queued"
  | "ringing"
  | "in-progress"
  | "completed"
  | "busy"
  | "failed"
  | "no-answer"
  | "canceled";

/** mock の Webhook の本文 */
export interface SimulatedEvent {
  readonly eventId: string;
  readonly providerCallId: string;
  /** 発信時に渡された通話 ID（プロバイダのメタデータとして返ってくるもの） */
  readonly callId: string;
  readonly status: MockRawStatus;
  readonly answeredBy?: "human" | "machine";
  readonly occurredAt: string;
}

const RAW_TO_STATUS: Readonly<Record<MockRawStatus, CallStatus>> = {
  queued: "DIALING",
  ringing: "RINGING",
  "in-progress": "IN_PROGRESS",
  completed: "ENDED",
  busy: "BUSY",
  failed: "FAILED",
  "no-answer": "NO_ANSWER",
  canceled: "CANCELED",
};

/** プロバイダの語彙をドメインの状態に正規化する。知らない値は undefined（推測で状態を作らない） */
export function normalizeMockStatus(raw: string): CallStatus | undefined {
  return Object.hasOwn(RAW_TO_STATUS, raw) ? RAW_TO_STATUS[raw as MockRawStatus] : undefined;
}

const SCRIPTS: Readonly<
  Record<
    Exclude<SimulatedScenario, "PROVIDER_ERROR">,
    readonly (readonly [MockRawStatus, ("human" | "machine")?])[]
  >
> = {
  ANSWER: [["ringing"], ["in-progress", "human"], ["completed"]],
  BUSY: [["busy"]],
  REJECT: [["ringing"], ["failed"]],
  NO_ANSWER: [["ringing"], ["no-answer"]],
  DISCONNECT: [["ringing"], ["in-progress", "human"], ["failed"]],
  VOICEMAIL: [["ringing"], ["in-progress", "machine"], ["completed"]],
};

export class MockProviderError extends Error {
  constructor() {
    super("mock provider returned 503 Service Unavailable");
    this.name = "MockProviderError";
  }
}

/**
 * 本物の電話はかけない、テスト・ローカル・デモ用のプロバイダ（ADR-0004）。
 * Phase 8 でシミュレーターに拡張：発信のたびにシナリオどおりの Webhook のイベント列を作る（届けるのはテスト側）。
 */
export class MockTelephonyProvider implements TelephonyProvider {
  readonly name = "mock";
  readonly placed: CreateProviderCallRequest[] = [];
  readonly transfers: { providerCallId: string; to: string }[] = [];
  private readonly byKey = new Map<string, string>();
  private readonly statuses = new Map<string, CallStatus>();
  private readonly outbox = new Map<string, SimulatedEvent[]>();
  private readonly idPrefix: string;
  private readonly now: () => Date;
  private scenario: SimulatedScenario | undefined;
  private seq = 0;

  constructor(opts: { idPrefix?: string; now?: () => Date } = {}) {
    this.idPrefix = opts.idPrefix === undefined ? "MOCK" : `MOCK-${opts.idPrefix}`;
    this.now = opts.now ?? (() => new Date());
  }

  /** 次の 1 件の発信のシナリオ（その後は ANSWER に戻る） */
  nextScenario(scenario: SimulatedScenario): void {
    this.scenario = scenario;
  }

  async createCall(request: CreateProviderCallRequest): Promise<ProviderCall> {
    const known = this.byKey.get(request.idempotencyKey);
    if (known) return this.view(known);
    const scenario = this.scenario ?? "ANSWER";
    this.scenario = undefined;
    this.placed.push(request);
    if (scenario === "PROVIDER_ERROR") throw new MockProviderError();
    const id = `${this.idPrefix}-${this.placed.length}`;
    this.byKey.set(request.idempotencyKey, id);
    this.statuses.set(id, "DIALING");
    const start = this.now().getTime();
    this.outbox.set(
      id,
      SCRIPTS[scenario].map(([status, answeredBy], i) => {
        this.seq += 1;
        return {
          eventId: `${id}:evt-${this.seq}`,
          providerCallId: id,
          callId: request.callId,
          status,
          ...(answeredBy ? { answeredBy } : {}),
          occurredAt: new Date(start + (i + 1) * 1000).toISOString(),
        };
      }),
    );
    return this.view(id);
  }

  /** まだ届けていない Webhook のイベントを取り出す（順序・重複・遅延はテスト側で作る） */
  takeEvents(providerCallId: string): SimulatedEvent[] {
    const events = this.outbox.get(providerCallId) ?? [];
    this.outbox.set(providerCallId, []);
    return events;
  }

  /** 時刻（occurredAt）が来たイベントを全通話から古い順に取り出す（local で自分の API へ自動で届ける用） */
  takeDueEvents(now: Date): SimulatedEvent[] {
    const due: SimulatedEvent[] = [];
    for (const [id, events] of this.outbox) {
      const ready = events.filter((e) => new Date(e.occurredAt) <= now);
      if (ready.length === 0) continue;
      due.push(...ready);
      this.outbox.set(
        id,
        events.filter((e) => !ready.includes(e)),
      );
    }
    return due.sort((a, b) => a.occurredAt.localeCompare(b.occurredAt));
  }

  async endCall(providerCallId: string): Promise<void> {
    this.setStatus(providerCallId, "ENDED");
  }

  async transferCall(providerCallId: string, target: TransferTarget): Promise<void> {
    this.require(providerCallId);
    this.transfers.push({ providerCallId, to: target.to });
  }

  async getCall(providerCallId: string): Promise<ProviderCall> {
    return this.view(providerCallId);
  }

  /** テストから回線の状態変化（Webhook 相当）を起こす */
  setStatus(providerCallId: string, status: CallStatus): void {
    this.require(providerCallId);
    this.statuses.set(providerCallId, status);
  }

  private require(providerCallId: string): void {
    if (!this.statuses.has(providerCallId)) throw new Error(`unknown call ${providerCallId}`);
  }

  private view(providerCallId: string): ProviderCall {
    this.require(providerCallId);
    return {
      provider: this.name,
      providerCallId,
      status: this.statuses.get(providerCallId) ?? "FAILED",
    };
  }
}

// ---- mock の Webhook の署名（`x-tac-signature: t=<unix 秒>,v1=<hex HMAC-SHA256(secret, "t.body")>`） ----

export const MOCK_SIGNATURE_HEADER = "x-tac-signature";
const SIGNATURE_TOLERANCE_SECONDS = 5 * 60;

const mac = (secret: string, timestamp: number, body: string) =>
  createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");

export function signMockWebhook(secret: string, body: string, at: Date): string {
  const t = Math.floor(at.getTime() / 1000);
  return `t=${t},v1=${mac(secret, t, body)}`;
}

/** 署名とタイムスタンプ（前後 5 分）を検証する。比較は定数時間 */
export function verifyMockWebhook(
  secret: string,
  body: string,
  header: string | undefined,
  now: Date,
): boolean {
  const match = header?.match(/^t=(\d{1,12}),v1=([0-9a-f]{64})$/);
  if (!match) return false;
  const t = Number(match[1]);
  if (Math.abs(now.getTime() / 1000 - t) > SIGNATURE_TOLERANCE_SECONDS) return false;
  const expected = Buffer.from(mac(secret, t, body), "hex");
  const actual = Buffer.from(match[2] ?? "", "hex");
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

export type AppEnv = "local" | "test" | "staging" | "production";
export type ProviderName = "mock" | "twilio" | "openai-sip";

export class ProviderNotAllowedError extends Error {
  constructor(provider: ProviderName, appEnv: AppEnv) {
    super(`telephony provider "${provider}" is not allowed in "${appEnv}" (only "mock")`);
    this.name = "ProviderNotAllowedError";
  }
}

/** local / test では本物の回線につながるアダプタを作れないようにする（テストから実際に発信しない保証）。 */
export function createTelephonyProvider(config: {
  appEnv: AppEnv;
  provider: ProviderName;
}): TelephonyProvider {
  if (config.provider === "mock") return new MockTelephonyProvider({ idPrefix: randomUUID() });
  if (config.appEnv === "local" || config.appEnv === "test") {
    throw new ProviderNotAllowedError(config.provider, config.appEnv);
  }
  throw new Error(`telephony provider "${config.provider}" is not implemented yet (Phase 10/11)`);
}
