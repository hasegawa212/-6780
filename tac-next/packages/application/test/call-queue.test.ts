import { describe, expect, it } from "vitest";
import { CallQueueQuery } from "../src/call-queue.js";
import { jst, ORG_A, phone, setup } from "./support.js";

function withTwoDueFollowUps() {
  const ctx = setup();
  for (const contactId of ["c-1", "c-2"]) {
    void ctx.deps.followUps.insert({
      id: `fu-${contactId}`,
      organizationId: ORG_A,
      contactId,
      kind: "FOLLOW_UP",
      dueAt: jst("2026-10-05T09:00:00"),
      status: "OPEN",
      sourceCallId: "older-call",
    });
  }
  return ctx;
}

describe("CallQueueQuery — 抑止の確認は fail closed", () => {
  it("lists due follow-ups for contactable people", async () => {
    const { deps } = withTwoDueFollowUps();
    const items = await new CallQueueQuery(deps).listDue(ORG_A);
    expect(items.map((i) => i.contactId)).toEqual(["c-1", "c-2"]);
  });

  it("drops only the contact whose suppression lookup fails, and keeps the rest", async () => {
    const { deps } = withTwoDueFollowUps();
    const original = deps.suppression.canContact.bind(deps.suppression);
    deps.suppression.canContact = async (org, p) => {
      if (p === phone("090-0000-0001")) throw new Error("lookup failed");
      return original(org, p);
    };
    const items = await new CallQueueQuery(deps).listDue(ORG_A);
    expect(items.map((i) => i.contactId)).toEqual(["c-2"]);
  });

  it("treats a non-boolean answer as suppressed", async () => {
    const { deps } = withTwoDueFollowUps();
    deps.suppression.canContact = async () => 1 as unknown as boolean;
    expect(await new CallQueueQuery(deps).listDue(ORG_A)).toEqual([]);
  });
});
