import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  CALL_STATUSES,
  type CallStatus,
  canTransitionCall,
  isTerminalCall,
  reconcileProviderStatus,
} from "../src/call-status.js";

describe("call lifecycle", () => {
  it.each<[CallStatus, CallStatus]>([
    ["REQUESTED", "DIALING"],
    ["REQUESTED", "FAILED"],
    ["REQUESTED", "CANCELED"],
    ["DIALING", "RINGING"],
    ["RINGING", "IN_PROGRESS"],
    ["RINGING", "NO_ANSWER"],
    ["RINGING", "BUSY"],
    ["IN_PROGRESS", "ENDED"],
    ["IN_PROGRESS", "FAILED"],
  ])("allows %s → %s", (from, to) => {
    expect(canTransitionCall(from, to)).toBe(true);
  });

  it.each<[CallStatus, CallStatus]>([
    ["REQUESTED", "IN_PROGRESS"], // 呼び出しを飛ばして通話中にはならない
    ["RINGING", "DIALING"], // 後戻りしない
    ["IN_PROGRESS", "RINGING"],
    ["ENDED", "IN_PROGRESS"],
    ["CANCELED", "DIALING"],
  ])("forbids %s → %s", (from, to) => {
    expect(canTransitionCall(from, to)).toBe(false);
  });

  it("no transition ever leaves a terminal state", () => {
    const status = fc.constantFrom(...CALL_STATUSES);
    fc.assert(
      fc.property(status, status, (from, to) => {
        if (isTerminalCall(from)) expect(canTransitionCall(from, to)).toBe(false);
      }),
    );
  });
});

describe("reconcileProviderStatus (out-of-order / duplicate webhooks)", () => {
  it("applies a forward transition", () => {
    expect(reconcileProviderStatus("DIALING", "RINGING")).toEqual({
      status: "RINGING",
      applied: true,
    });
  });

  it("ignores a late event that would move the call backwards", () => {
    expect(reconcileProviderStatus("ENDED", "RINGING")).toEqual({
      status: "ENDED",
      applied: false,
    });
  });

  it("treats a duplicate as a no-op", () => {
    expect(reconcileProviderStatus("RINGING", "RINGING")).toEqual({
      status: "RINGING",
      applied: false,
    });
  });

  it("accepts a skipped-ahead terminal event (e.g. ENDED arrives before IN_PROGRESS)", () => {
    expect(reconcileProviderStatus("RINGING", "ENDED")).toEqual({
      status: "ENDED",
      applied: true,
    });
  });

  it("never regresses: any event sequence ends in a state at least as far along", () => {
    const status = fc.constantFrom(...CALL_STATUSES);
    fc.assert(
      fc.property(fc.array(status, { maxLength: 20 }), (events) => {
        let current: CallStatus = "REQUESTED";
        let reachedTerminal = false;
        for (const e of events) {
          const before = current;
          current = reconcileProviderStatus(current, e).status;
          if (reachedTerminal) expect(current).toBe(before);
          if (isTerminalCall(current)) reachedTerminal = true;
        }
      }),
    );
  });
});
