import { type E164, toE164 } from "@tac/domain";
import type { Deps } from "../src/deps.js";
import type { Campaign, Contact, OrganizationId, UserId } from "../src/ports.js";
import {
  FixedClock,
  ImmediateUnitOfWork,
  InMemoryAuditLog,
  InMemoryCalls,
  InMemoryCampaigns,
  InMemoryConsents,
  InMemoryContacts,
  InMemoryEvents,
  InMemoryFollowUps,
  InMemoryOrganizations,
  InMemoryOutcomes,
  InMemorySuppression,
  RecordingTelephony,
  SequentialIds,
} from "../src/testing/in-memory.js";

export const ORG_A = "org-a" as OrganizationId;
export const ORG_B = "org-b" as OrganizationId;
export const OPERATOR = "user-op" as UserId;

export const phone = (raw: string): E164 => {
  const r = toE164(raw);
  if (!r.ok) throw new Error(`bad test phone ${raw}`);
  return r.value;
};

export const jst = (local: string) => new Date(`${local}+09:00`);

/** 架空のデータだけを使う（実在の人物・番号は使わない）。 */
export function setup() {
  const clock = new FixedClock(jst("2026-10-05T10:00:00")); // 月曜 10:00
  const deps = {
    clock,
    ids: new SequentialIds(),
    organizations: new InMemoryOrganizations(),
    contacts: new InMemoryContacts(),
    campaigns: new InMemoryCampaigns(),
    calls: new InMemoryCalls(),
    outcomes: new InMemoryOutcomes(),
    followUps: new InMemoryFollowUps(),
    suppression: new InMemorySuppression(),
    consents: new InMemoryConsents(),
    audit: new InMemoryAuditLog(),
    events: new InMemoryEvents(),
    uow: new ImmediateUnitOfWork(),
    telephony: new RecordingTelephony(),
  } satisfies Deps;

  for (const org of [ORG_A, ORG_B]) {
    deps.organizations.rows.set(org, {
      id: org,
      companyName: org === ORG_A ? "株式会社サンプル不動産" : "テスト住宅株式会社",
      aiVoiceOutboundEnabled: false,
    });
  }
  const contact = (id: string, org: OrganizationId, raw: string): Contact => ({
    id,
    organizationId: org,
    displayName: `架空 ${id}`,
    phone: phone(raw),
    timeZone: "Asia/Tokyo",
  });
  deps.contacts.rows.set("c-1", contact("c-1", ORG_A, "090-0000-0001"));
  deps.contacts.rows.set("c-2", contact("c-2", ORG_A, "090-0000-0002"));
  deps.contacts.rows.set("c-b", contact("c-b", ORG_B, "090-0000-0009"));

  const campaign = (id: string, org: OrganizationId): Campaign => ({
    id,
    organizationId: org,
    product: "新築マンション",
    callerId: phone("03-0000-0000"),
    callingWindow: {
      timeZone: "Asia/Tokyo",
      startMinute: 9 * 60,
      endMinute: 20 * 60,
      weekdays: [1, 2, 3, 4, 5, 6],
      holidays: [],
    },
    allowedCountryCodes: ["81"],
    dailyCap: 100,
    perNumberDailyLimit: 1,
    maxAttempts: 3,
  });
  deps.campaigns.rows.set("camp-1", campaign("camp-1", ORG_A));
  deps.campaigns.rows.set("camp-b", campaign("camp-b", ORG_B));

  const callCommand = (over: Partial<Record<string, unknown>> = {}) => ({
    organizationId: ORG_A,
    actorId: OPERATOR,
    contactId: "c-1",
    campaignId: "camp-1",
    idempotencyKey: "key-1",
    mode: "HUMAN_DIALED" as const,
    agentName: "佐藤",
    ...over,
  });

  return { deps, clock, callCommand };
}
