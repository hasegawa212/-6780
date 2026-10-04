import type {
  CallingWindowPolicy,
  CallMode,
  CallStatus,
  E164,
  FollowUpKind,
  OutcomeCode,
} from "@tac/domain";

/**
 * アプリケーション層のポート。永続化・時計・電話プロバイダはここで抽象化し、
 * 実装（インメモリ / PostgreSQL / Twilio …）はアダプタ側に置く。
 * すべてのリポジトリ操作は organizationId で絞り込む（テナント分離）。
 */

export type OrganizationId = string & { readonly __brand: "OrganizationId" };
export type UserId = string & { readonly __brand: "UserId" };

export interface Clock {
  now(): Date;
}

export interface IdGenerator {
  next(): string;
}

export interface Organization {
  readonly id: OrganizationId;
  readonly companyName: string;
  /** AI が話す発信（ADR-0003）。既定 false */
  readonly aiVoiceOutboundEnabled: boolean;
}

export interface Contact {
  readonly id: string;
  readonly organizationId: OrganizationId;
  readonly displayName: string;
  readonly phone: E164;
  /** 相手のタイムゾーン（不明なら undefined → キャンペーンの既定） */
  readonly timeZone: string | undefined;
}

export interface Campaign {
  readonly id: string;
  readonly organizationId: OrganizationId;
  readonly product: string;
  readonly callerId: E164;
  readonly callingWindow: CallingWindowPolicy;
  readonly allowedCountryCodes: readonly string[];
  /** 直近24時間の発信上限（null は上限なし） */
  readonly dailyCap: number | null;
  readonly perNumberDailyLimit: number;
  readonly maxAttempts: number;
}

export interface CallRecord {
  readonly id: string;
  readonly organizationId: OrganizationId;
  readonly contactId: string;
  readonly campaignId: string;
  readonly to: E164;
  readonly from: E164;
  readonly mode: CallMode;
  readonly status: CallStatus;
  readonly idempotencyKey: string;
  /** 同じ冪等キーで別内容の要求が来たことを見分けるための要求内容のハッシュ */
  readonly requestFingerprint: string;
  readonly requestedBy: UserId;
  readonly agentName: string;
  readonly provider: string | undefined;
  readonly providerCallId: string | undefined;
  readonly createdAt: Date;
}

export interface OutcomeRecord {
  readonly callId: string;
  readonly organizationId: OrganizationId;
  readonly code: OutcomeCode;
  readonly recordedBy: UserId;
  readonly recordedAt: Date;
}

export interface FollowUpRecord {
  readonly id: string;
  readonly organizationId: OrganizationId;
  readonly contactId: string;
  readonly kind: FollowUpKind;
  readonly dueAt: Date;
  readonly status: "OPEN" | "DONE" | "CANCELED";
  readonly sourceCallId: string;
}

export interface AuditEntry {
  readonly organizationId: OrganizationId;
  readonly actorId: UserId;
  readonly action: string;
  readonly resource: string;
  readonly at: Date;
  readonly before?: unknown;
  readonly after?: unknown;
}

export interface DomainEvent {
  readonly type: string;
  readonly version: 1;
  readonly organizationId: OrganizationId;
  readonly occurredAt: Date;
  readonly payload: Readonly<Record<string, unknown>>;
}

export class DuplicateIdempotencyKeyError extends Error {
  constructor() {
    super("duplicate idempotency key");
    this.name = "DuplicateIdempotencyKeyError";
  }
}

export interface OrganizationRepository {
  get(id: OrganizationId): Promise<Organization | undefined>;
}

export interface ContactRepository {
  get(organizationId: OrganizationId, id: string): Promise<Contact | undefined>;
}

export interface CampaignRepository {
  get(organizationId: OrganizationId, id: string): Promise<Campaign | undefined>;
}

export interface CallRepository {
  get(organizationId: OrganizationId, id: string): Promise<CallRecord | undefined>;
  findByIdempotencyKey(
    organizationId: OrganizationId,
    key: string,
  ): Promise<CallRecord | undefined>;
  /** (organizationId, idempotencyKey) が既にあれば DuplicateIdempotencyKeyError を投げる */
  insert(call: CallRecord): Promise<void>;
  update(call: CallRecord): Promise<void>;
  /** since 以降に回線へ発信を依頼した件数（REQUESTED 以降、CANCELED を除く） */
  countDialedSince(organizationId: OrganizationId, since: Date): Promise<number>;
  countToNumberSince(organizationId: OrganizationId, to: E164, since: Date): Promise<number>;
  countForContact(organizationId: OrganizationId, contactId: string): Promise<number>;
}

export interface OutcomeRepository {
  get(organizationId: OrganizationId, callId: string): Promise<OutcomeRecord | undefined>;
  insert(outcome: OutcomeRecord): Promise<void>;
}

export interface FollowUpRepository {
  insert(followUp: FollowUpRecord): Promise<void>;
  listOpenDue(organizationId: OrganizationId, now: Date): Promise<readonly FollowUpRecord[]>;
  cancelOpenForContact(organizationId: OrganizationId, contactId: string): Promise<number>;
}

/** 抑止（DNC）。DB のフラグではなくサービスとして扱い、発信の直前に必ず問い合わせる。 */
export interface SuppressionService {
  canContact(organizationId: OrganizationId, phone: E164): Promise<boolean>;
  add(entry: {
    organizationId: OrganizationId;
    phone: E164;
    reason: string;
    source: string;
    actorId: UserId;
  }): Promise<void>;
}

export interface ConsentRepository {
  hasValidConsent(
    organizationId: OrganizationId,
    contactId: string,
    scope: "AI_VOICE_OUTBOUND",
    at: Date,
  ): Promise<boolean>;
}

export interface AuditLog {
  append(entry: AuditEntry): Promise<void>;
}

export interface EventPublisher {
  publish(event: DomainEvent): Promise<void>;
}

/**
 * 複数の書き込みを1つのトランザクションにまとめる。
 * 結果の記録と抑止の登録が片方だけ成功する、という状態を作らないために使う。
 */
export interface UnitOfWork {
  run<T>(work: () => Promise<T>): Promise<T>;
}

// ---- 電話プロバイダ（ADR-0004） ----

export interface CreateProviderCallRequest {
  readonly idempotencyKey: string;
  readonly to: E164;
  readonly from: E164;
  readonly callId: string;
  readonly disclosureText: string;
}

export interface ProviderCall {
  readonly provider: string;
  readonly providerCallId: string;
  readonly status: CallStatus;
}

export type TransferTarget = { readonly kind: "PHONE"; readonly to: E164 };

export class ProviderTimeoutError extends Error {
  constructor() {
    super("telephony provider timed out; the call may or may not have been placed");
    this.name = "ProviderTimeoutError";
  }
}

export interface TelephonyProvider {
  readonly name: string;
  createCall(request: CreateProviderCallRequest): Promise<ProviderCall>;
  endCall(providerCallId: string): Promise<void>;
  transferCall(providerCallId: string, target: TransferTarget): Promise<void>;
  getCall(providerCallId: string): Promise<ProviderCall>;
}
