import { describe, expect, it } from "vitest";
import { withDeploymentGate } from "../src/deployment-gate.js";
import { InMemorySafetyControls } from "../src/testing/in-memory.js";

describe("withDeploymentGate: 設定で発信が OFF なら、全発信停止として扱う", () => {
  it("OUTBOUND_CALLS_ENABLED=false なら、組織の停止スイッチに関係なく停止", async () => {
    const inner = new InMemorySafetyControls();
    expect(await withDeploymentGate(false, inner).isOutboundStopped()).toBe(true);
  });

  it("ON のときは、組織の停止スイッチに従う", async () => {
    const inner = new InMemorySafetyControls();
    const gated = withDeploymentGate(true, inner);
    expect(await gated.isOutboundStopped()).toBe(false);
    inner.stopAllOutbound();
    expect(await gated.isOutboundStopped()).toBe(true);
  });

  it("停止スイッチの照会に失敗したら停止扱い（fail closed）", async () => {
    const broken = {
      isOutboundStopped: async () => {
        throw new Error("db down");
      },
    };
    expect(await withDeploymentGate(true, broken).isOutboundStopped()).toBe(true);
  });
});
