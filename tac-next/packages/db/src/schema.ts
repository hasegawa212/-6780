import type { CallingWindowPolicy } from "@tac/domain";
import {
  bigint,
  boolean,
  integer,
  jsonb,
  pgTable,
  text,
  timestamp,
  uuid,
} from "drizzle-orm/pg-core";

/**
 * 型付きクエリのためのテーブル定義。**正はマイグレーションの SQL**（migrations/*.sql）で、
 * 制約・インデックス・RLS はそちらにだけ書く。列のずれは migrate.test.ts が検出する。
 */

const ts = (name: string) => timestamp(name, { withTimezone: true, mode: "date" });

export const organizations = pgTable("organizations", {
  id: uuid("id").primaryKey(),
  companyName: text("company_name").notNull(),
  aiVoiceOutboundEnabled: boolean("ai_voice_outbound_enabled").notNull().default(false),
  paused: boolean("paused").notNull().default(false),
  maxConcurrentCalls: integer("max_concurrent_calls").notNull(),
  createdAt: ts("created_at").notNull().defaultNow(),
});

export const contacts = pgTable("contacts", {
  id: uuid("id").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  displayName: text("display_name").notNull(),
  phoneE164: text("phone_e164").notNull(),
  timeZone: text("time_zone"),
  createdAt: ts("created_at").notNull().defaultNow(),
});

export const campaigns = pgTable("campaigns", {
  id: uuid("id").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  product: text("product").notNull(),
  callerIdE164: text("caller_id_e164").notNull(),
  callingWindow: jsonb("calling_window").$type<CallingWindowPolicy>().notNull(),
  allowedCountryCodes: text("allowed_country_codes").array().notNull(),
  dailyCap: integer("daily_cap"),
  perNumberDailyLimit: integer("per_number_daily_limit").notNull(),
  maxAttempts: integer("max_attempts").notNull(),
  paused: boolean("paused").notNull().default(false),
  createdAt: ts("created_at").notNull().defaultNow(),
});

export const calls = pgTable("calls", {
  id: uuid("id").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  contactId: uuid("contact_id").notNull(),
  campaignId: uuid("campaign_id").notNull(),
  toE164: text("to_e164").notNull(),
  fromE164: text("from_e164").notNull(),
  mode: text("mode").notNull(),
  status: text("status").notNull(),
  idempotencyKey: text("idempotency_key").notNull(),
  requestFingerprint: text("request_fingerprint").notNull(),
  requestedBy: text("requested_by").notNull(),
  agentName: text("agent_name").notNull(),
  provider: text("provider"),
  providerCallId: text("provider_call_id"),
  createdAt: ts("created_at").notNull().defaultNow(),
  updatedAt: ts("updated_at").notNull().defaultNow(),
});

export const outcomes = pgTable("outcomes", {
  callId: uuid("call_id").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  code: text("code").notNull(),
  recordedBy: text("recorded_by").notNull(),
  recordedAt: ts("recorded_at").notNull(),
});

export const followUps = pgTable("follow_ups", {
  id: uuid("id").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  contactId: uuid("contact_id").notNull(),
  kind: text("kind").notNull(),
  dueAt: ts("due_at").notNull(),
  status: text("status").notNull(),
  sourceCallId: uuid("source_call_id").notNull(),
  createdAt: ts("created_at").notNull().defaultNow(),
});

export const suppressionEntries = pgTable("suppression_entries", {
  id: uuid("id").primaryKey().defaultRandom(),
  organizationId: uuid("organization_id").notNull(),
  phoneE164: text("phone_e164").notNull(),
  reason: text("reason").notNull(),
  source: text("source").notNull(),
  actorId: text("actor_id").notNull(),
  createdAt: ts("created_at").notNull().defaultNow(),
  liftedAt: ts("lifted_at"),
  liftedBy: text("lifted_by"),
  liftReason: text("lift_reason"),
});

export const consents = pgTable("consents", {
  id: uuid("id").primaryKey().defaultRandom(),
  organizationId: uuid("organization_id").notNull(),
  contactId: uuid("contact_id").notNull(),
  scope: text("scope").notNull(),
  legalBasis: text("legal_basis").notNull(),
  source: text("source").notNull(),
  grantedAt: ts("granted_at").notNull(),
  revokedAt: ts("revoked_at"),
});

export const auditLogs = pgTable("audit_logs", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedAlwaysAsIdentity(),
  organizationId: uuid("organization_id").notNull(),
  actorId: text("actor_id").notNull(),
  action: text("action").notNull(),
  resource: text("resource").notNull(),
  before: jsonb("before"),
  after: jsonb("after"),
  at: ts("at").notNull(),
});

export const systemControls = pgTable("system_controls", {
  id: boolean("id").primaryKey(),
  outboundStopped: boolean("outbound_stopped").notNull(),
  updatedAt: ts("updated_at").notNull().defaultNow(),
  updatedBy: text("updated_by"),
});

export const users = pgTable("users", {
  id: uuid("id").primaryKey(),
  email: text("email").notNull(),
  displayName: text("display_name").notNull(),
  passwordHash: text("password_hash").notNull(),
  createdAt: ts("created_at").notNull().defaultNow(),
  disabledAt: ts("disabled_at"),
});

export const memberships = pgTable("memberships", {
  organizationId: uuid("organization_id").notNull(),
  userId: uuid("user_id").notNull(),
  role: text("role").notNull(),
  createdAt: ts("created_at").notNull().defaultNow(),
});

export const sessions = pgTable("sessions", {
  idHash: text("id_hash").primaryKey(),
  organizationId: uuid("organization_id").notNull(),
  userId: uuid("user_id").notNull(),
  csrfHash: text("csrf_hash").notNull(),
  createdAt: ts("created_at").notNull(),
  expiresAt: ts("expires_at").notNull(),
  revokedAt: ts("revoked_at"),
});

export const webhookEvents = pgTable("webhook_events", {
  provider: text("provider").notNull(),
  eventId: text("event_id").notNull(),
  payload: jsonb("payload").notNull(),
  receivedAt: ts("received_at").notNull(),
  claimedAt: ts("claimed_at"),
  processedAt: ts("processed_at"),
});

export const callEvents = pgTable("call_events", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedAlwaysAsIdentity(),
  organizationId: uuid("organization_id").notNull(),
  callId: uuid("call_id").notNull(),
  provider: text("provider").notNull(),
  eventId: text("event_id").notNull(),
  status: text("status").notNull(),
  applied: boolean("applied").notNull(),
  occurredAt: ts("occurred_at").notNull(),
  receivedAt: ts("received_at").notNull(),
});

export const authThrottle = pgTable("auth_throttle", {
  key: text("key").primaryKey(),
  failures: integer("failures").notNull(),
  windowStartedAt: ts("window_started_at").notNull(),
  lockedUntil: ts("locked_until"),
});
