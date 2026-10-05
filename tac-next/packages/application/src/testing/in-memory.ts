import type { E164 } from "@tac/domain";
import {
  type AuditEntry,
  type AuditLog,
  type BudgetService,
  type CallRecord,
  type CallRepository,
  type Campaign,
  type CampaignRepository,
  type Clock,
  type ConsentRepository,
  type Contact,
  type ContactRepository,
  type CreateProviderCallRequest,
  type DomainEvent,
  DuplicateIdempotencyKeyError,
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
  ProviderTimeoutError,
  type SafetyControls,
  type SuppressionService,
  type TelephonyProvider,
  type UnitOfWork,
} from "../ports.js";

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

export class InMemoryContacts implements ContactRepository {
  readonly rows = new Map<string, Contact>();
  async get(org: OrganizationId, id: string) {
    const c = this.rows.get(id);
    return c?.organizationId === org ? c : undefined;
  }
}

export class InMemoryCampaigns implements CampaignRepository {
  readonly rows = new Map<string, Campaign>();
  async get(org: OrganizationId, id: string) {
    const c = this.rows.get(id);
    return c?.organizationId === org ? c : undefined;
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
    this.rows.set(call.id, structuredClone(call));
  }
  async update(call: CallRecord) {
    if (!this.rows.has(call.id)) throw new Error(`call ${call.id} not found`);
    this.rows.set(call.id, structuredClone(call));
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
  async countActive(org: OrganizationId) {
    const active = new Set(["REQUESTED", "DIALING", "RINGING", "IN_PROGRESS"]);
    return byOrg(this.rows.values(), org).filter((r) => active.has(r.status)).length;
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
    if (this.rows.has(outcome.callId)) throw new Error("outcome already recorded");
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
  run<T>(work: () => Promise<T>): Promise<T> {
    return work();
  }
}

/** 呼び出しを記録するだけのプロバイダ（本物の電話はかけない）。 */
export class RecordingTelephony implements TelephonyProvider {
  readonly name = "recording";
  readonly requests: CreateProviderCallRequest[] = [];
  mode: "ok" | "error" | "timeout" = "ok";
  async createCall(request: CreateProviderCallRequest): Promise<ProviderCall> {
    this.requests.push(request);
    if (this.mode === "error") throw new Error("provider rejected the call");
    if (this.mode === "timeout") throw new ProviderTimeoutError();
    return { provider: this.name, providerCallId: `PC-${this.requests.length}`, status: "DIALING" };
  }
  async endCall(): Promise<void> {}
  async transferCall(): Promise<void> {}
  async getCall(providerCallId: string): Promise<ProviderCall> {
    return { provider: this.name, providerCallId, status: "DIALING" };
  }
}
