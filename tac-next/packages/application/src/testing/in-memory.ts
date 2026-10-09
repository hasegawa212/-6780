import type { CallStatus, E164 } from "@tac/domain";
import {
  type ActiveSession,
  type AuthDirectory,
  type LoginCandidate,
  type LoginThrottle,
  normalizeEmail,
  type PasswordHasher,
  type Role,
  type SecretTokens,
  type SessionStore,
  type ThrottlePolicy,
} from "../auth.js";
import {
  ActiveCallExistsError,
  type AuditEntry,
  type AuditLog,
  type BudgetService,
  type CallEventLog,
  type CallEventRecord,
  type CallRecord,
  type CallRepository,
  type Campaign,
  type CampaignRepository,
  type Clock,
  type ConsentRepository,
  type Contact,
  type ContactCursor,
  type ContactRepository,
  type CreateProviderCallRequest,
  type DomainEvent,
  DuplicateIdempotencyKeyError,
  DuplicateOutcomeError,
  type EventPublisher,
  type FollowUpRecord,
  type FollowUpRepository,
  type IdGenerator,
  type Organization,
  type OrganizationId,
  type OrganizationRepository,
  type OutcomeRecord,
  type OutcomeRepository,
  type ProviderCall,
  type ProviderCallCandidate,
  type ProviderCallLocator,
  type ProviderCallQuery,
  type ProviderEventInbox,
  ProviderRejectedError,
  ProviderTimeoutError,
  type SafetyControls,
  type SuppressionService,
  type TelephonyProvider,
  type UncertainCallFinder,
  type UnitOfWork,
  type UserId,
} from "../ports.js";

const ACTIVE_STATUSES: ReadonlySet<string> = new Set([
  "REQUESTED",
  "DIALING",
  "RINGING",
  "IN_PROGRESS",
]);

/**
 * テスト・ローカル用のインメモリ実装。PostgreSQL 実装（Phase 2）と同じ契約テストを通す前提。
 * 外部から渡された値は structuredClone で複製して保持し、呼び出し側の変更が漏れないようにする。
 */

export class FixedClock implements Clock {
  constructor(private current: Date) {}
  now(): Date {
    return new Date(this.current);
  }
  set(t: Date): void {
    this.current = t;
  }
}

export class SequentialIds implements IdGenerator {
  private n = 0;
  constructor(private readonly prefix = "id") {}
  next(): string {
    this.n += 1;
    return `${this.prefix}-${this.n}`;
  }
}

const byOrg = <T extends { organizationId: OrganizationId }>(
  rows: Iterable<T>,
  org: OrganizationId,
) => [...rows].filter((r) => r.organizationId === org);

export class InMemoryOrganizations implements OrganizationRepository {
  readonly rows = new Map<string, Organization>();
  async get(id: OrganizationId) {
    return this.rows.get(id);
  }
}

const byNameThenId = (
  a: { displayName: string; id: string },
  b: { displayName: string; id: string },
) =>
  a.displayName < b.displayName
    ? -1
    : a.displayName > b.displayName
      ? 1
      : a.id < b.id
        ? -1
        : a.id > b.id
          ? 1
          : 0;

export class InMemoryContacts implements ContactRepository {
  readonly rows = new Map<string, Contact>();
  async get(org: OrganizationId, id: string) {
    const c = this.rows.get(id);
    return c?.organizationId === org ? c : undefined;
  }
  async list(org: OrganizationId, page: { limit: number; after: ContactCursor | undefined }) {
    const { after } = page;
    return byOrg(this.rows.values(), org)
      .sort(byNameThenId)
      .filter((c) => !after || byNameThenId(c, after) > 0)
      .slice(0, page.limit);
  }
}

export class InMemoryCampaigns implements CampaignRepository {
  readonly rows = new Map<string, Campaign>();
  async get(org: OrganizationId, id: string) {
    const c = this.rows.get(id);
    return c?.organizationId === org ? c : undefined;
  }
  async list(org: OrganizationId) {
    return byOrg(this.rows.values(), org);
  }
}

export class InMemoryCalls implements CallRepository {
  readonly rows = new Map<string, CallRecord>();
  async get(org: OrganizationId, id: string) {
    const c = this.rows.get(id);
    return c?.organizationId === org ? structuredClone(c) : undefined;
  }
  async findByIdempotencyKey(org: OrganizationId, key: string) {
    const c = byOrg(this.rows.values(), org).find((r) => r.idempotencyKey === key);
    return c ? structuredClone(c) : undefined;
  }
  async insert(call: CallRecord) {
    // PostgreSQL の UNIQUE (organization_id, idempotency_key) と同じ振る舞い
    if (
      byOrg(this.rows.values(), call.organizationId).some(
        (r) => r.idempotencyKey === call.idempotencyKey,
      )
    ) {
      throw new DuplicateIdempotencyKeyError();
    }
    // PostgreSQL の部分一意インデックス（回線上の通話は番号ごとに 1 件）と同じ振る舞い
    if (
      byOrg(this.rows.values(), call.organizationId).some(
        (r) => r.to === call.to && ACTIVE_STATUSES.has(r.status),
      )
    ) {
      throw new ActiveCallExistsError();
    }
    this.rows.set(call.id, structuredClone(call));
  }
  async update(call: CallRecord) {
    if (!this.rows.has(call.id)) throw new Error(`call ${call.id} not found`);
    this.rows.set(call.id, structuredClone(call));
  }
  async transitionStatus(org: OrganizationId, id: string, expected: CallStatus, next: CallStatus) {
    const c = this.rows.get(id);
    if (c?.organizationId !== org || c.status !== expected) return false;
    this.rows.set(id, { ...c, status: next });
    return true;
  }
  async attachProvider(org: OrganizationId, id: string, provider: string, providerCallId: string) {
    const c = this.rows.get(id);
    if (c?.organizationId !== org || c.providerCallId !== undefined) return;
    this.rows.set(id, { ...c, provider, providerCallId });
  }
  async countDialedSince(org: OrganizationId, since: Date) {
    return byOrg(this.rows.values(), org).filter(
      (r) => r.createdAt >= since && r.status !== "CANCELED",
    ).length;
  }
  async countToNumberSince(org: OrganizationId, to: E164, since: Date) {
    return byOrg(this.rows.values(), org).filter(
      (r) => r.to === to && r.createdAt >= since && r.status !== "CANCELED",
    ).length;
  }
  async countForContact(org: OrganizationId, contactId: string) {
    return byOrg(this.rows.values(), org).filter((r) => r.contactId === contactId).length;
  }
  async countActive(org: OrganizationId, staleRequestedBefore: Date) {
    return byOrg(this.rows.values(), org).filter(
      (r) =>
        ACTIVE_STATUSES.has(r.status) &&
        !(r.status === "REQUESTED" && r.createdAt.getTime() < staleRequestedBefore.getTime()),
    ).length;
  }
}

/** PostgreSQL の UNIQUE (provider, event_id) と同じく、同じイベントは1件だけ残す */
export class InMemoryCallEvents implements CallEventLog {
  readonly entries: CallEventRecord[] = [];
  async append(event: CallEventRecord) {
    if (this.entries.some((e) => e.provider === event.provider && e.eventId === event.eventId))
      return;
    this.entries.push(structuredClone(event));
  }
}

export class InMemoryProviderEventInbox implements ProviderEventInbox {
  readonly rows = new Map<
    string,
    { payload: unknown; receivedAt: Date; state: "CLAIMED" | "RELEASED" | "PROCESSED" }
  >();
  async begin(e: { provider: string; eventId: string; receivedAt: Date; payload: unknown }) {
    const key = `${e.provider}:${e.eventId}`;
    const row = this.rows.get(key);
    if (row && row.state !== "RELEASED") return false;
    this.rows.set(key, {
      payload: structuredClone(e.payload),
      receivedAt: e.receivedAt,
      state: "CLAIMED",
    });
    return true;
  }
  async complete(provider: string, eventId: string) {
    const row = this.rows.get(`${provider}:${eventId}`);
    if (row) row.state = "PROCESSED";
  }
  async release(provider: string, eventId: string) {
    const row = this.rows.get(`${provider}:${eventId}`);
    if (row?.state === "CLAIMED") row.state = "RELEASED";
  }
}

export class InMemoryProviderCallLocator implements ProviderCallLocator {
  constructor(private readonly calls: InMemoryCalls) {}
  async locate(provider: string, providerCallId: string | undefined, callId: string | undefined) {
    const rows = [...this.calls.rows.values()];
    const found =
      (providerCallId !== undefined &&
        rows.find((r) => r.provider === provider && r.providerCallId === providerCallId)) ||
      (callId !== undefined &&
        rows.find((r) => r.id === callId && (r.provider === undefined || r.provider === provider)));
    return found ? { organizationId: found.organizationId, callId: found.id } : undefined;
  }
}

export class InMemorySafetyControls implements SafetyControls {
  private stopped = false;
  stopAllOutbound(): void {
    this.stopped = true;
  }
  resumeOutbound(): void {
    this.stopped = false;
  }
  async isOutboundStopped() {
    return this.stopped;
  }
}

export class InMemoryBudget implements BudgetService {
  private readonly byOrg = new Map<string, number>();
  set(org: OrganizationId, remainingJpy: number): void {
    this.byOrg.set(org, remainingJpy);
  }
  async remaining(org: OrganizationId) {
    return this.byOrg.get(org) ?? null;
  }
}

export class InMemoryOutcomes implements OutcomeRepository {
  readonly rows = new Map<string, OutcomeRecord>();
  async get(org: OrganizationId, callId: string) {
    const o = this.rows.get(callId);
    return o?.organizationId === org ? o : undefined;
  }
  async insert(outcome: OutcomeRecord) {
    if (this.rows.has(outcome.callId)) throw new DuplicateOutcomeError();
    this.rows.set(outcome.callId, structuredClone(outcome));
  }
}

export class InMemoryFollowUps implements FollowUpRepository {
  readonly rows = new Map<string, FollowUpRecord>();
  async insert(f: FollowUpRecord) {
    this.rows.set(f.id, structuredClone(f));
  }
  async listOpenDue(org: OrganizationId, now: Date) {
    return byOrg(this.rows.values(), org)
      .filter((f) => f.status === "OPEN" && f.dueAt <= now)
      .sort((a, b) => a.dueAt.getTime() - b.dueAt.getTime());
  }
  async cancelOpenForContact(org: OrganizationId, contactId: string) {
    let n = 0;
    for (const f of byOrg(this.rows.values(), org)) {
      if (f.contactId === contactId && f.status === "OPEN") {
        this.rows.set(f.id, { ...f, status: "CANCELED" });
        n += 1;
      }
    }
    return n;
  }
}

type SuppressionEntry = Parameters<SuppressionService["add"]>[0];

export class InMemorySuppression implements SuppressionService {
  readonly entries: SuppressionEntry[] = [];
  async canContact(org: OrganizationId, phone: E164) {
    return !this.entries.some((e) => e.organizationId === org && e.phone === phone);
  }
  async add(entry: SuppressionEntry) {
    if (await this.canContact(entry.organizationId, entry.phone)) {
      this.entries.push({ ...entry });
    }
  }
}

export class InMemoryConsents implements ConsentRepository {
  readonly granted = new Set<string>();
  grant(org: OrganizationId, contactId: string) {
    this.granted.add(`${org}:${contactId}`);
  }
  async hasValidConsent(org: OrganizationId, contactId: string) {
    return this.granted.has(`${org}:${contactId}`);
  }
}

export class InMemoryAuditLog implements AuditLog {
  readonly entries: AuditEntry[] = [];
  async append(entry: AuditEntry) {
    this.entries.push(structuredClone(entry));
  }
}

export class InMemoryEvents implements EventPublisher {
  readonly events: DomainEvent[] = [];
  async publish(event: DomainEvent) {
    this.events.push(structuredClone(event));
  }
  types(): string[] {
    return this.events.map((e) => e.type);
  }
}

/** インメモリでは単に順に実行する（ロールバックは PostgreSQL 実装で保証する）。 */
export class ImmediateUnitOfWork implements UnitOfWork {
  private readonly tails = new Map<string, Promise<unknown>>();
  run<T>(work: () => Promise<T>): Promise<T> {
    return work();
  }
  /** 組織ごとの Promise の鎖で直列化する（PostgreSQL の advisory lock と同じ振る舞い） */
  runExclusive<T>(organizationId: OrganizationId, work: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(organizationId) ?? Promise.resolve();
    const current = previous.then(work);
    const tail = current.catch(() => undefined);
    this.tails.set(organizationId, tail);
    void tail.then(() => {
      if (this.tails.get(organizationId) === tail) this.tails.delete(organizationId);
    });
    return current;
  }
}

/** 呼び出しを記録するだけのプロバイダ（本物の電話はかけない）。 */
export class RecordingTelephony implements TelephonyProvider {
  readonly name = "recording";
  readonly requests: CreateProviderCallRequest[] = [];
  mode: "ok" | "error" | "timeout" = "ok";
  async createCall(request: CreateProviderCallRequest): Promise<ProviderCall> {
    this.requests.push(request);
    if (this.mode === "error") throw new ProviderRejectedError();
    if (this.mode === "timeout") throw new ProviderTimeoutError();
    return { provider: this.name, providerCallId: `PC-${this.requests.length}`, status: "DIALING" };
  }
  async endCall(): Promise<void> {}
  async transferCall(): Promise<void> {}
  async getCall(providerCallId: string): Promise<ProviderCall> {
    return { provider: this.name, providerCallId, status: "DIALING" };
  }
  /** 照合のテスト用：プロバイダの通話一覧に見えている通話 */
  listed: ProviderCallCandidate[] = [];
  readonly queries: ProviderCallQuery[] = [];
  findMode: "ok" | "error" = "ok";
  async findCalls(query: ProviderCallQuery): Promise<readonly ProviderCallCandidate[]> {
    this.queries.push(query);
    if (this.findMode === "error") throw new Error("provider list unavailable");
    return this.listed.filter((c) => c.createdAt >= query.createdAfter);
  }
}

/** 0006 の list_uncertain_calls と同じ条件（REQUESTED・プロバイダの ID なし・olderThan より前）で古い順 */
export class InMemoryUncertainCallFinder implements UncertainCallFinder {
  constructor(private readonly calls: InMemoryCalls) {}
  async list(olderThan: Date, limit: number) {
    return [...this.calls.rows.values()]
      .filter(
        (r) =>
          r.status === "REQUESTED" &&
          r.providerCallId === undefined &&
          r.createdAt.getTime() < olderThan.getTime(),
      )
      .sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime())
      .slice(0, limit)
      .map((r) => ({ organizationId: r.organizationId, callId: r.id }));
  }
}

// ---- 認証（Phase 3） ----

interface DirectoryUser {
  userId: UserId;
  email: string;
  displayName: string;
  passwordHash: string;
  disabled: boolean;
  memberships: { organizationId: OrganizationId; role: Role }[];
}

export class InMemoryAuthDirectory implements AuthDirectory {
  readonly users = new Map<string, DirectoryUser>();
  add(
    u: Omit<DirectoryUser, "disabled" | "memberships"> & {
      memberships: { organizationId: OrganizationId; role: Role }[];
    },
  ) {
    this.users.set(u.userId, { ...u, email: normalizeEmail(u.email), disabled: false });
  }
  disable(userId: UserId) {
    const u = this.users.get(userId);
    if (u) u.disabled = true;
  }
  removeMembership(userId: UserId, org: OrganizationId) {
    const u = this.users.get(userId);
    if (u) u.memberships = u.memberships.filter((m) => m.organizationId !== org);
  }
  setRole(userId: UserId, org: OrganizationId, role: Role) {
    const m = this.users.get(userId)?.memberships.find((x) => x.organizationId === org);
    if (m) m.role = role;
  }
  async findForLogin(email: string): Promise<LoginCandidate | undefined> {
    const u = [...this.users.values()].find((x) => x.email === email && !x.disabled);
    return (
      u && {
        userId: u.userId,
        displayName: u.displayName,
        passwordHash: u.passwordHash,
        memberships: u.memberships.map((m) => ({ ...m })),
      }
    );
  }
}

export class InMemorySessions implements SessionStore {
  readonly rows = new Map<
    string,
    Parameters<SessionStore["create"]>[0] & { revokedAt: Date | undefined }
  >();
  constructor(private readonly directory: InMemoryAuthDirectory) {}
  async create(s: Parameters<SessionStore["create"]>[0]) {
    this.rows.set(s.idHash, { ...s, revokedAt: undefined });
  }
  async resolve(idHash: string, now: Date): Promise<ActiveSession | undefined> {
    const s = this.rows.get(idHash);
    if (!s || s.revokedAt || s.expiresAt <= now) return undefined;
    const u = this.directory.users.get(s.userId);
    const m = u?.memberships.find((x) => x.organizationId === s.organizationId);
    if (!u || u.disabled || !m) return undefined;
    return {
      userId: s.userId,
      organizationId: s.organizationId,
      role: m.role,
      displayName: u.displayName,
      csrfHash: s.csrfHash,
      expiresAt: s.expiresAt,
    };
  }
  async revoke(idHash: string, at: Date) {
    const s = this.rows.get(idHash);
    if (s && !s.revokedAt) s.revokedAt = at;
  }
}

/** テスト専用：`plain:<パスワード>` を照合するだけ（本番は scrypt、apps/api） */
export class PlainTextPasswordHasher implements PasswordHasher {
  verifications = 0;
  async verify(password: string, hash: string) {
    this.verifications += 1;
    return hash === `plain:${password}`;
  }
  async verifyDummy() {
    this.verifications += 1;
  }
}

/** テスト専用：連番のトークンと、見分けやすいハッシュ表現 */
export class SequentialTokens implements SecretTokens {
  private n = 0;
  newToken() {
    this.n += 1;
    return `tok-${this.n}`;
  }
  hash(token: string) {
    return `H:${[...token].reverse().join("")}`;
  }
}

/** ログイン試行の記録（PostgreSQL の auth_throttle_* 関数と同じ振る舞い） */
export class InMemoryLoginThrottle implements LoginThrottle {
  readonly rows = new Map<
    string,
    { failures: number; windowStartedAt: Date; lockedUntil: Date | undefined }
  >();
  async lockedUntil(keys: readonly string[], now: Date) {
    let latest: Date | undefined;
    for (const k of keys) {
      const until = this.rows.get(k)?.lockedUntil;
      if (until && until > now && (!latest || until > latest)) latest = until;
    }
    return latest;
  }
  async recordFailure(key: string, policy: ThrottlePolicy, now: Date) {
    const row = this.rows.get(key);
    const fresh = !row || now.getTime() - row.windowStartedAt.getTime() >= policy.windowMs;
    const failures = fresh ? 1 : row.failures + 1;
    const windowStartedAt = fresh ? now : row.windowStartedAt;
    this.rows.set(key, {
      failures: failures >= policy.maxFailures ? 0 : failures,
      windowStartedAt: failures >= policy.maxFailures ? now : windowStartedAt,
      lockedUntil:
        failures >= policy.maxFailures ? new Date(now.getTime() + policy.lockMs) : row?.lockedUntil,
    });
  }
  async reserve(key: string, policy: ThrottlePolicy, now: Date) {
    const row = this.rows.get(key);
    if (row?.lockedUntil && row.lockedUntil > now) return row.lockedUntil;
    const fresh = !row || now.getTime() - row.windowStartedAt.getTime() >= policy.windowMs;
    const failures = fresh ? 1 : row.failures + 1;
    this.rows.set(
      key,
      failures >= policy.maxFailures
        ? {
            failures: 0,
            windowStartedAt: now,
            lockedUntil: new Date(now.getTime() + policy.lockMs),
          }
        : { failures, windowStartedAt: fresh ? now : row.windowStartedAt, lockedUntil: undefined },
    );
    return undefined;
  }
  async refund(key: string, policy: ThrottlePolicy, now: Date) {
    const row = this.rows.get(key);
    if (!row) return;
    // ロック中で数がリセット済み＝直前の予約が上限に達してロックした。戻すと上限の 1 つ手前になる
    const undoLock = row.lockedUntil !== undefined && row.lockedUntil > now && row.failures === 0;
    this.rows.set(
      key,
      undoLock
        ? { ...row, failures: policy.maxFailures - 1, lockedUntil: undefined }
        : { ...row, failures: Math.max(0, row.failures - 1) },
    );
  }
  async reset(key: string) {
    this.rows.delete(key);
  }
}
