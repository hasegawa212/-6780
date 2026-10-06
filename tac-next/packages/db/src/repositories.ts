import { randomUUID } from "node:crypto";
import {
  ActiveCallExistsError,
  type AuditEntry,
  type AuditLog,
  type CallRecord,
  type CallRepository,
  type Campaign,
  type CampaignRepository,
  type ConsentRepository,
  type Contact,
  type ContactRepository,
  type Deps,
  DuplicateIdempotencyKeyError,
  DuplicateOutcomeError,
  type FollowUpRecord,
  type FollowUpRepository,
  type IdGenerator,
  type Organization,
  type OrganizationId,
  type OrganizationRepository,
  type OutcomeRecord,
  type OutcomeRepository,
  type SafetyControls,
  type SuppressionService,
  type UnitOfWork,
  type UserId,
} from "@tac/application";
import type { CallMode, CallStatus, E164, FollowUpKind, OutcomeCode } from "@tac/domain";
import { and, asc, count, eq, gt, gte, inArray, isNull, lte, ne, or, sql } from "drizzle-orm";
import * as t from "./schema.js";
import { isUuid, type TenantScope } from "./tenant.js";

/**
 * アプリケーション層のポートの PostgreSQL 実装。
 * テナント分離は二重：各クエリの WHERE organization_id（ここ）＋ RLS（migrations/0002）。
 * UUID でない ID は「見つからない」として扱う（DB の型エラーを外へ漏らさない）。
 */

const ACTIVE_STATUSES = ["REQUESTED", "DIALING", "RINGING", "IN_PROGRESS"] as const;

/** ドライバの例外（Drizzle が cause で包む）から PostgreSQL のエラーコードと制約名を取り出す */
function pgError(e: unknown): { code: string; constraint: string | undefined } | undefined {
  for (let cur: unknown = e, depth = 0; cur && depth < 5; depth += 1) {
    if (typeof cur === "object" && "code" in cur && typeof cur.code === "string") {
      const constraint =
        "constraint" in cur && typeof cur.constraint === "string" ? cur.constraint : undefined;
      if (/^[0-9A-Z]{5}$/.test(cur.code)) return { code: cur.code, constraint };
    }
    cur = typeof cur === "object" && "cause" in cur ? cur.cause : undefined;
  }
  return undefined;
}

export class PgOrganizations implements OrganizationRepository {
  constructor(private readonly scope: TenantScope) {}
  async get(id: OrganizationId): Promise<Organization | undefined> {
    if (!isUuid(id)) return undefined;
    const [row] = await this.scope.withTenant(id, (tx) =>
      tx.select().from(t.organizations).where(eq(t.organizations.id, id)),
    );
    return (
      row && {
        id: row.id as OrganizationId,
        companyName: row.companyName,
        aiVoiceOutboundEnabled: row.aiVoiceOutboundEnabled,
        paused: row.paused,
        maxConcurrentCalls: row.maxConcurrentCalls,
      }
    );
  }
}

export class PgContacts implements ContactRepository {
  constructor(private readonly scope: TenantScope) {}
  async get(org: OrganizationId, id: string): Promise<Contact | undefined> {
    if (!isUuid(org) || !isUuid(id)) return undefined;
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.contacts)
        .where(and(eq(t.contacts.organizationId, org), eq(t.contacts.id, id))),
    );
    return (
      row && {
        id: row.id,
        organizationId: row.organizationId as OrganizationId,
        displayName: row.displayName,
        phone: row.phoneE164 as E164,
        timeZone: row.timeZone ?? undefined,
      }
    );
  }
}

export class PgCampaigns implements CampaignRepository {
  constructor(private readonly scope: TenantScope) {}
  async get(org: OrganizationId, id: string): Promise<Campaign | undefined> {
    if (!isUuid(org) || !isUuid(id)) return undefined;
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.campaigns)
        .where(and(eq(t.campaigns.organizationId, org), eq(t.campaigns.id, id))),
    );
    return (
      row && {
        id: row.id,
        organizationId: row.organizationId as OrganizationId,
        product: row.product,
        callerId: row.callerIdE164 as E164,
        callingWindow: row.callingWindow,
        allowedCountryCodes: row.allowedCountryCodes,
        dailyCap: row.dailyCap,
        perNumberDailyLimit: row.perNumberDailyLimit,
        maxAttempts: row.maxAttempts,
        paused: row.paused,
      }
    );
  }
}

type CallRow = typeof t.calls.$inferSelect;

const toCallRecord = (row: CallRow): CallRecord => ({
  id: row.id,
  organizationId: row.organizationId as OrganizationId,
  contactId: row.contactId,
  campaignId: row.campaignId,
  to: row.toE164 as E164,
  from: row.fromE164 as E164,
  mode: row.mode as CallMode,
  status: row.status as CallStatus,
  idempotencyKey: row.idempotencyKey,
  requestFingerprint: row.requestFingerprint,
  requestedBy: row.requestedBy as UserId,
  agentName: row.agentName,
  provider: row.provider ?? undefined,
  providerCallId: row.providerCallId ?? undefined,
  createdAt: row.createdAt,
});

export class PgCalls implements CallRepository {
  constructor(private readonly scope: TenantScope) {}

  async get(org: OrganizationId, id: string) {
    if (!isUuid(org) || !isUuid(id)) return undefined;
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.calls)
        .where(and(eq(t.calls.organizationId, org), eq(t.calls.id, id))),
    );
    return row && toCallRecord(row);
  }

  async findByIdempotencyKey(org: OrganizationId, key: string) {
    if (!isUuid(org)) return undefined;
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.calls)
        .where(and(eq(t.calls.organizationId, org), eq(t.calls.idempotencyKey, key))),
    );
    return row && toCallRecord(row);
  }

  /**
   * 冪等キーの一意制約を ON CONFLICT の対象にする。両方の制約に当たるときも、
   * PostgreSQL は対象の制約を先に調べるので、契約どおり DuplicateIdempotencyKeyError が優先される。
   * 回線上の通話の部分一意インデックスに当たったら ActiveCallExistsError。
   */
  async insert(call: CallRecord): Promise<void> {
    try {
      const inserted = await this.scope.withTenant(call.organizationId, (tx) =>
        tx
          .insert(t.calls)
          .values({
            id: call.id,
            organizationId: call.organizationId,
            contactId: call.contactId,
            campaignId: call.campaignId,
            toE164: call.to,
            fromE164: call.from,
            mode: call.mode,
            status: call.status,
            idempotencyKey: call.idempotencyKey,
            requestFingerprint: call.requestFingerprint,
            requestedBy: call.requestedBy,
            agentName: call.agentName,
            provider: call.provider ?? null,
            providerCallId: call.providerCallId ?? null,
            createdAt: call.createdAt,
          })
          .onConflictDoNothing({ target: [t.calls.organizationId, t.calls.idempotencyKey] })
          .returning({ id: t.calls.id }),
      );
      if (inserted.length === 0) throw new DuplicateIdempotencyKeyError();
    } catch (e) {
      const pg = pgError(e);
      if (pg?.code === "23505" && pg.constraint === "calls_one_active_per_number_uq") {
        throw new ActiveCallExistsError();
      }
      throw e;
    }
  }

  /** 変えてよいのは状態とプロバイダの識別子だけ（宛先・冪等キーなどは作成時に確定） */
  async update(call: CallRecord): Promise<void> {
    const updated = await this.scope.withTenant(call.organizationId, (tx) =>
      tx
        .update(t.calls)
        .set({
          status: call.status,
          provider: call.provider ?? null,
          providerCallId: call.providerCallId ?? null,
          updatedAt: sql`now()`,
        })
        .where(and(eq(t.calls.organizationId, call.organizationId), eq(t.calls.id, call.id)))
        .returning({ id: t.calls.id }),
    );
    if (updated.length === 0) throw new Error(`call ${call.id} not found`);
  }

  private async count(org: OrganizationId, ...conditions: ReturnType<typeof eq>[]) {
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select({ n: count() })
        .from(t.calls)
        .where(and(eq(t.calls.organizationId, org), ...conditions)),
    );
    return row?.n ?? 0;
  }

  countDialedSince(org: OrganizationId, since: Date) {
    return this.count(org, gte(t.calls.createdAt, since), ne(t.calls.status, "CANCELED"));
  }

  countToNumberSince(org: OrganizationId, to: E164, since: Date) {
    return this.count(
      org,
      eq(t.calls.toE164, to),
      gte(t.calls.createdAt, since),
      ne(t.calls.status, "CANCELED"),
    );
  }

  countForContact(org: OrganizationId, contactId: string) {
    if (!isUuid(contactId)) return Promise.resolve(0);
    return this.count(org, eq(t.calls.contactId, contactId));
  }

  countActive(org: OrganizationId) {
    return this.count(org, inArray(t.calls.status, [...ACTIVE_STATUSES]));
  }
}

export class PgOutcomes implements OutcomeRepository {
  constructor(private readonly scope: TenantScope) {}

  async get(org: OrganizationId, callId: string) {
    if (!isUuid(org) || !isUuid(callId)) return undefined;
    const [row] = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.outcomes)
        .where(and(eq(t.outcomes.organizationId, org), eq(t.outcomes.callId, callId))),
    );
    return (
      row && {
        callId: row.callId,
        organizationId: row.organizationId as OrganizationId,
        code: row.code as OutcomeCode,
        recordedBy: row.recordedBy as UserId,
        recordedAt: row.recordedAt,
      }
    );
  }

  /** call_id が主キー。既にあれば DuplicateOutcomeError（例外でトランザクションを壊さない ON CONFLICT） */
  async insert(outcome: OutcomeRecord): Promise<void> {
    const inserted = await this.scope.withTenant(outcome.organizationId, (tx) =>
      tx
        .insert(t.outcomes)
        .values({
          callId: outcome.callId,
          organizationId: outcome.organizationId,
          code: outcome.code,
          recordedBy: outcome.recordedBy,
          recordedAt: outcome.recordedAt,
        })
        .onConflictDoNothing({ target: t.outcomes.callId })
        .returning({ callId: t.outcomes.callId }),
    );
    if (inserted.length === 0) throw new DuplicateOutcomeError();
  }
}

export class PgFollowUps implements FollowUpRepository {
  constructor(private readonly scope: TenantScope) {}

  async insert(f: FollowUpRecord): Promise<void> {
    await this.scope.withTenant(f.organizationId, (tx) =>
      tx.insert(t.followUps).values({
        id: f.id,
        organizationId: f.organizationId,
        contactId: f.contactId,
        kind: f.kind,
        dueAt: f.dueAt,
        status: f.status,
        sourceCallId: f.sourceCallId,
      }),
    );
  }

  async listOpenDue(org: OrganizationId, now: Date): Promise<readonly FollowUpRecord[]> {
    if (!isUuid(org)) return [];
    const rows = await this.scope.withTenant(org, (tx) =>
      tx
        .select()
        .from(t.followUps)
        .where(
          and(
            eq(t.followUps.organizationId, org),
            eq(t.followUps.status, "OPEN"),
            lte(t.followUps.dueAt, now),
          ),
        )
        .orderBy(asc(t.followUps.dueAt)),
    );
    return rows.map((r) => ({
      id: r.id,
      organizationId: r.organizationId as OrganizationId,
      contactId: r.contactId,
      kind: r.kind as FollowUpKind,
      dueAt: r.dueAt,
      status: r.status as FollowUpRecord["status"],
      sourceCallId: r.sourceCallId,
    }));
  }

  async cancelOpenForContact(org: OrganizationId, contactId: string): Promise<number> {
    const rows = await this.scope.withTenant(org, (tx) =>
      tx
        .update(t.followUps)
        .set({ status: "CANCELED" })
        .where(
          and(
            eq(t.followUps.organizationId, org),
            eq(t.followUps.contactId, contactId),
            eq(t.followUps.status, "OPEN"),
          ),
        )
        .returning({ id: t.followUps.id }),
    );
    return rows.length;
  }
}

/** 抑止（DNC）。有効な抑止（lifted_at IS NULL）が1件でもあれば発信できない */
export class PgSuppression implements SuppressionService {
  constructor(private readonly scope: TenantScope) {}

  async canContact(org: OrganizationId, phone: E164): Promise<boolean> {
    const rows = await this.scope.withTenant(org, (tx) =>
      tx
        .select({ id: t.suppressionEntries.id })
        .from(t.suppressionEntries)
        .where(
          and(
            eq(t.suppressionEntries.organizationId, org),
            eq(t.suppressionEntries.phoneE164, phone),
            isNull(t.suppressionEntries.liftedAt),
          ),
        )
        .limit(1),
    );
    return rows.length === 0;
  }

  async add(entry: Parameters<SuppressionService["add"]>[0]): Promise<void> {
    await this.scope.withTenant(entry.organizationId, (tx) =>
      tx
        .insert(t.suppressionEntries)
        .values({
          id: randomUUID(),
          organizationId: entry.organizationId,
          phoneE164: entry.phone,
          reason: entry.reason,
          source: entry.source,
          actorId: entry.actorId,
        })
        .onConflictDoNothing({
          target: [t.suppressionEntries.organizationId, t.suppressionEntries.phoneE164],
          where: isNull(t.suppressionEntries.liftedAt),
        }),
    );
  }
}

export class PgConsents implements ConsentRepository {
  constructor(private readonly scope: TenantScope) {}
  async hasValidConsent(
    org: OrganizationId,
    contactId: string,
    scope: "AI_VOICE_OUTBOUND",
    at: Date,
  ): Promise<boolean> {
    if (!isUuid(org) || !isUuid(contactId)) return false;
    const rows = await this.scope.withTenant(org, (tx) =>
      tx
        .select({ id: t.consents.id })
        .from(t.consents)
        .where(
          and(
            eq(t.consents.organizationId, org),
            eq(t.consents.contactId, contactId),
            eq(t.consents.scope, scope),
            lte(t.consents.grantedAt, at),
            or(isNull(t.consents.revokedAt), gt(t.consents.revokedAt, at)),
          ),
        )
        .limit(1),
    );
    return rows.length > 0;
  }
}

export class PgAuditLog implements AuditLog {
  constructor(private readonly scope: TenantScope) {}
  async append(entry: AuditEntry): Promise<void> {
    await this.scope.withTenant(entry.organizationId, (tx) =>
      tx.insert(t.auditLogs).values({
        organizationId: entry.organizationId,
        actorId: entry.actorId,
        action: entry.action,
        resource: entry.resource,
        before: entry.before ?? null,
        after: entry.after ?? null,
        at: entry.at,
      }),
    );
  }
}

/** 全発信停止。行が無い・読めないときは「止まっている」と答える（fail closed） */
export class PgSafetyControls implements SafetyControls {
  constructor(private readonly scope: TenantScope) {}
  async isOutboundStopped(): Promise<boolean> {
    const [row] = await this.scope.withTenant(undefined, (tx) =>
      tx
        .select({ stopped: t.systemControls.outboundStopped })
        .from(t.systemControls)
        .where(eq(t.systemControls.id, true)),
    );
    return row?.stopped !== false;
  }
}

export class PgUnitOfWork implements UnitOfWork {
  constructor(private readonly scope: TenantScope) {}
  run<T>(work: () => Promise<T>): Promise<T> {
    return this.scope.run(work);
  }
  runExclusive<T>(organizationId: OrganizationId, work: () => Promise<T>): Promise<T> {
    return this.scope.runExclusive(organizationId, work);
  }
}

/** 本番用の ID（UUID v4） */
export class UuidIds implements IdGenerator {
  next(): string {
    return randomUUID();
  }
}

/** 永続化に関わる Deps を PostgreSQL で組み立てる（時計・電話・イベント・予算は呼び出し側で渡す） */
export function createPgDeps(scope: TenantScope) {
  return {
    organizations: new PgOrganizations(scope),
    contacts: new PgContacts(scope),
    campaigns: new PgCampaigns(scope),
    calls: new PgCalls(scope),
    outcomes: new PgOutcomes(scope),
    followUps: new PgFollowUps(scope),
    suppression: new PgSuppression(scope),
    consents: new PgConsents(scope),
    audit: new PgAuditLog(scope),
    safety: new PgSafetyControls(scope),
    uow: new PgUnitOfWork(scope),
  } satisfies Partial<Deps>;
}
