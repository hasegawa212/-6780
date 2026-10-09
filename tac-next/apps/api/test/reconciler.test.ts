import type { ReconcileResult } from "@tac/application";
import { describe, expect, it } from "vitest";
import { createReconcileLoop } from "../src/reconciler.js";

/* 確定しない発信の照合を定期的に回す小さなループ（ADR-0016） */

describe("createReconcileLoop", () => {
  it("結果を種類ごとの件数だけでログに出す（通話 ID・電話番号は出さない）", async () => {
    const logs: string[] = [];
    const loop = createReconcileLoop(
      async (): Promise<ReconcileResult> => ({
        results: [
          { callId: "c1", kind: "MATCHED" },
          { callId: "c2", kind: "NOT_PLACED" },
          { callId: "c3", kind: "MATCHED" },
        ],
      }),
      (m) => logs.push(m),
    );
    await loop.tick();
    expect(logs).toEqual(["reconcile: MATCHED=2 NOT_PLACED=1"]);
  });

  it("何も無ければログを出さない", async () => {
    const logs: string[] = [];
    await createReconcileLoop(
      async () => ({ results: [] }),
      (m) => logs.push(m),
    ).tick();
    expect(logs).toEqual([]);
  });

  it("前の回が終わっていなければ重ねて走らせない", async () => {
    let running = 0;
    let max = 0;
    let release: () => void = () => {};
    const loop = createReconcileLoop(
      async () => {
        running += 1;
        max = Math.max(max, running);
        await new Promise<void>((r) => {
          release = r;
        });
        running -= 1;
        return { results: [] };
      },
      () => {},
    );
    const first = loop.tick();
    await loop.tick();
    release();
    await first;
    expect(max).toBe(1);
  });

  it("失敗してもループは止まらず、理由だけをログに出す", async () => {
    const logs: string[] = [];
    let calls = 0;
    const loop = createReconcileLoop(
      async () => {
        calls += 1;
        if (calls === 1) throw new Error("db down +819000000001");
        return { results: [{ callId: "c", kind: "PENDING" }] };
      },
      (m) => logs.push(m),
    );
    await loop.tick();
    await loop.tick();
    expect(logs[0]).toMatch(/^reconcile failed/);
    expect(logs[0]).not.toContain("819000000001");
    expect(logs[1]).toBe("reconcile: PENDING=1");
  });
});
