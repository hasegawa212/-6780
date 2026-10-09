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
  /** 組織単位の一時停止（ADR-0006） */
  readonly paused: boolean;
  /** 組織全体で同時に回線へ乗せてよい通話の数（担当者の人数が目安） */
  readonly maxConcurrentCalls: number;
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
  readonly paused: boolean;
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

/**
 * 同じ組織・同じ番号に、回線に乗っている通話（REQUESTED・DIALING・RINGING・IN_PROGRESS）が既にある。
 * 別々の冪等キーで同時に発信要求が来ても二重発信しないための制約（QA-NX-01）。
 * PostgreSQL では部分一意インデックス
 * `UNIQUE (organization_id, to_number) WHERE status IN ('REQUESTED','DIALING','RINGING','IN_PROGRESS')` で保証する。
 */
export class ActiveCallExistsError extends Error {
  constructor() {
    super("an active call to this number already exists");
    this.name = "ActiveCallExistsError";
  }
}

/** 同じ通話の結果が既に記録されている（PostgreSQL では outcomes.call_id の主キー） */
export class DuplicateOutcomeError extends Error {
  constructor() {
    super("outcome already recorded");
    this.name = "DuplicateOutcomeError";
  }
}

export interface OrganizationRepository {
  get(id: OrganizationId): Promise<Organization | undefined>;
}

/** 名前順の一覧のカーソル（直前のページの最後の行） */
export interface ContactCursor {
  readonly displayName: string;
  readonly id: string;
}

export interface ContactRepository {
  get(organizationId: OrganizationId, id: string): Promise<Contact | undefined>;
  /** (displayName, id) の順。after の次の行から最大 limit 件 */
  list(
    organizationId: OrganizationId,
    page: { limit: number; after: ContactCursor | undefined },
  ): Promise<readonly Contact[]>;
}

export interface CampaignRepository {
  get(organizationId: OrganizationId, id: string): Promise<Campaign | undefined>;
  list(organizationId: OrganizationId): Promise<readonly Campaign[]>;
}

export interface CallRepository {
  get(organizationId: OrganizationId, id: string): Promise<CallRecord | undefined>;
  findByIdempotencyKey(
    organizationId: OrganizationId,
    key: string,
  ): Promise<CallRecord | undefined>;
  /**
   * (organizationId, idempotencyKey) が既にあれば DuplicateIdempotencyKeyError、
   * 同じ番号に回線上の通話が既にあれば ActiveCallExistsError を投げる（この順で判定する）。
   */
  insert(call: CallRecord): Promise<void>;
  /** テスト・移行用の上書き。ユースケースは transitionStatus / attachProvider を使う（同時更新で後退させない） */
  update(call: CallRecord): Promise<void>;
  /**
   * 現在の状態が expected のときだけ next に変える（compare-and-set）。変えたら true。
   * Webhook と発信の応答が同時に状態を書いても、古い読み取りで上書きしないための操作。
   */
  transitionStatus(
    organizationId: OrganizationId,
    callId: string,
    expected: CallStatus,
    next: CallStatus,
  ): Promise<boolean>;
  /** プロバイダの識別子を記録する。既に記録済みなら何もしない（別の値で上書きしない） */
  attachProvider(
    organizationId: OrganizationId,
    callId: string,
    provider: string,
    providerCallId: string,
  ): Promise<void>;
  /** since 以降に回線へ発信を依頼した件数（REQUESTED 以降、CANCELED を除く） */
  countDialedSince(organizationId: OrganizationId, since: Date): Promise<number>;
  countToNumberSince(organizationId: OrganizationId, to: E164, since: Date): Promise<number>;
  countForContact(organizationId: OrganizationId, contactId: string): Promise<number>;
  /**
   * 回線に乗っている（REQUESTED・DIALING・RINGING・IN_PROGRESS）通話の数。
   * ただし staleRequestedBefore より前から REQUESTED のまま確定しない通話（発信されたか分からないまま
   * Webhook が来ないもの）は同時通話数に数えない（IQA-08）。番号ごとの「回線上 1 件」の制約には残るので、
   * その相手へ掛け直して二重発信になることはない。
   */
  countActive(organizationId: OrganizationId, staleRequestedBefore: Date): Promise<number>;
}

export interface OutcomeRepository {
  get(organizationId: OrganizationId, callId: string): Promise<OutcomeRecord | undefined>;
  /** 同じ通話の結果が既にあれば DuplicateOutcomeError を投げる */
  insert(outcome: OutcomeRecord): Promise<void>;
}

/** 通話のイベント（プロバイダの状態通知）の記録。(provider, eventId) ごとに1件（2件目以降は捨てる） */
export interface CallEventRecord {
  readonly organizationId: OrganizationId;
  readonly callId: string;
  readonly provider: string;
  readonly eventId: string;
  readonly status: CallStatus;
  /** 状態に反映したか（重複・後戻りで無視したら false） */
  readonly applied: boolean;
  readonly occurredAt: Date;
  readonly receivedAt: Date;
}

export interface CallEventLog {
  append(event: CallEventRecord): Promise<void>;
}

/**
 * 受け取った Webhook の受信箱（`(provider, eventId)` で重複排除、生データを保存）。
 * begin はイベントの処理権を取る：初めて・または前回の処理が失敗して未処理のときだけ true。
 * 処理済み・いま別の処理が実行中なら false（重複として扱う）。
 */
export interface ProviderEventInbox {
  begin(event: {
    provider: string;
    eventId: string;
    receivedAt: Date;
    payload: unknown;
  }): Promise<boolean>;
  complete(provider: string, eventId: string, at: Date): Promise<void>;
  /** 処理に失敗したので処理権を手放す（再送で処理し直せるようにする） */
  release(provider: string, eventId: string): Promise<void>;
}

/**
 * プロバイダの通知から、どの組織のどの通話かを特定する（テナントをまたぐ唯一の照会）。
 * (provider, providerCallId) で探し、無ければ発信時に渡した callId で探す（プロバイダが一致しないものは返さない）。
 */
export interface ProviderCallLocator {
  locate(
    provider: string,
    providerCallId: string | undefined,
    callId: string | undefined,
  ): Promise<{ organizationId: OrganizationId; callId: string } | undefined>;
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

/** システム全体の緊急停止（STOP ALL OUTBOUND CALLS）。 */
export interface SafetyControls {
  isOutboundStopped(): Promise<boolean>;
}

/** 予算の残り（円）。null は予算を設定していない。 */
export interface BudgetService {
  remaining(organizationId: OrganizationId, campaignId: string): Promise<number | null>;
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
  /**
   * run と同じく1つのトランザクションで実行し、さらに同じ組織の runExclusive どうしを直列化する。
   * 1日上限・同時通話数のような「数えてから保存する」判定を、同時要求で超えないために使う。
   * PostgreSQL では `pg_advisory_xact_lock`（組織 ID のハッシュ）で、コミット / ロールバックで解放される。
   * 外部 I/O（電話プロバイダ）を中で呼ばないこと（ロックを長く握らない）。
   */
  runExclusive<T>(organizationId: OrganizationId, work: () => Promise<T>): Promise<T>;
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

/**
 * プロバイダが発信を確実に「受け付けなかった」ことを示す失敗（4xx・明示的な拒否など）。
 * これ以外の例外（接続断・5xx 応答の途中切れ等）は、発信されたかどうか分からないものとして扱う（IQA-03）。
 */
export class ProviderRejectedError extends Error {
  constructor(message = "telephony provider rejected the call") {
    super(message);
    this.name = "ProviderRejectedError";
  }
}

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
  /**
   * プロバイダ側の通話一覧から、この発信元からこの相手への最近の発信を探す（確定しない発信の照合、ADR-0016）。
   * 一覧を引けないプロバイダは実装しない（照合しない＝番号はふさがったまま）。
   */
  findCalls?(query: ProviderCallQuery): Promise<readonly ProviderCallCandidate[]>;
}

export interface ProviderCallQuery {
  readonly to: E164;
  readonly from: E164;
  /** この時刻以降に作られた通話だけ */
  readonly createdAfter: Date;
}

export interface ProviderCallCandidate {
  readonly providerCallId: string;
  /** プロバイダの状態をドメインの状態にしたもの。知らない値は undefined */
  readonly status: CallStatus | undefined;
  readonly createdAt: Date;
}

/**
 * 確定しない発信（REQUESTED のまま・プロバイダの ID なし）を、全組織から古い順に探す（照合の入口。テナントをまたぐ照会）。
 */
export interface UncertainCallFinder {
  list(
    olderThan: Date,
    limit: number,
  ): Promise<readonly { organizationId: OrganizationId; callId: string }[]>;
}
