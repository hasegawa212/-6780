import { inspect } from "node:util";
import { describe, expect, it } from "vitest";
import { loadConfig, Secret } from "../src/index.js";

const PROD = {
  APP_ENV: "production",
  DATABASE_URL: "postgres://tac:pw@db.internal:5432/tac",
  SESSION_SECRET: "s".repeat(32),
};

const issuesOf = (env: Record<string, string | undefined>) => {
  const r = loadConfig(env);
  return r.ok ? [] : r.error.map((i) => i.path);
};

describe("loadConfig", () => {
  it("starts with safe defaults when nothing is set", () => {
    const r = loadConfig({});
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.appEnv).toBe("local");
    expect(r.value.port).toBe(8080);
    expect(r.value.telephony.provider).toBe("mock");
    // 安全装置は既定で ON（現行 TAC の「既定 OFF」を繰り返さない）
    expect(r.value.safety.verifyWebhookSignatures).toBe(true);
    expect(r.value.safety.enforceCallingWindow).toBe(true);
  });

  it("reports which field is invalid instead of crashing", () => {
    expect(issuesOf({ PORT: "abc" })).toEqual(["PORT"]);
    expect(issuesOf({ APP_ENV: "prod" })).toEqual(["APP_ENV"]);
    expect(issuesOf({ VERIFY_WEBHOOK_SIGNATURES: "maybe" })).toEqual(["VERIFY_WEBHOOK_SIGNATURES"]);
  });

  it("allows only the mock telephony provider in local and test", () => {
    expect(issuesOf({ APP_ENV: "test", TELEPHONY_PROVIDER: "twilio" })).toContain(
      "TELEPHONY_PROVIDER",
    );
    expect(issuesOf({ APP_ENV: "local", TELEPHONY_PROVIDER: "openai-sip" })).toContain(
      "TELEPHONY_PROVIDER",
    );
  });

  it("requires a database and a strong session secret in staging and production", () => {
    expect(issuesOf({ APP_ENV: "staging" })).toEqual(
      expect.arrayContaining(["DATABASE_URL", "SESSION_SECRET"]),
    );
    expect(issuesOf({ ...PROD, SESSION_SECRET: "short" })).toEqual(["SESSION_SECRET"]);
    expect(loadConfig(PROD).ok).toBe(true);
  });

  it("requires Twilio credentials when the Twilio provider is selected", () => {
    expect(issuesOf({ ...PROD, TELEPHONY_PROVIDER: "twilio" })).toEqual(
      expect.arrayContaining(["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]),
    );
    expect(
      loadConfig({
        ...PROD,
        TELEPHONY_PROVIDER: "twilio",
        TWILIO_ACCOUNT_SID: `AC${"0".repeat(32)}`,
        TWILIO_AUTH_TOKEN: "x".repeat(32),
      }).ok,
    ).toBe(true);
  });

  it("refuses to disable safety controls in production", () => {
    expect(issuesOf({ ...PROD, VERIFY_WEBHOOK_SIGNATURES: "false" })).toEqual([
      "VERIFY_WEBHOOK_SIGNATURES",
    ]);
    expect(issuesOf({ ...PROD, ENFORCE_CALLING_WINDOW: "false" })).toEqual([
      "ENFORCE_CALLING_WINDOW",
    ]);
    // 開発ではテストのために外せる
    expect(loadConfig({ VERIFY_WEBHOOK_SIGNATURES: "false" }).ok).toBe(true);
  });

  it("never exposes secrets through String, JSON or util.inspect", () => {
    const r = loadConfig(PROD);
    if (!r.ok) throw new Error("expected ok");
    const dumped = [
      String(r.value.sessionSecret),
      JSON.stringify(r.value),
      inspect(r.value, { depth: 5 }),
    ];
    for (const text of dumped) {
      expect(text).not.toContain("s".repeat(32));
      expect(text).not.toContain("pw@");
    }
    expect(r.value.sessionSecret?.reveal()).toBe("s".repeat(32));
  });

  it("does not echo secret values in validation errors", () => {
    const r = loadConfig({ ...PROD, SESSION_SECRET: "tooshort-secret" });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(JSON.stringify(r.error)).not.toContain("tooshort-secret");
  });
});

describe("loadConfig: 危険な機能は明示的に ON にしない限り動かない", () => {
  it("発信・自動発信・AI 音声・録音は、すべて既定で OFF", () => {
    const r = loadConfig({});
    if (!r.ok) throw new Error("expected ok");
    expect(r.value.features).toEqual({
      outboundCalls: false,
      autoDial: false,
      aiVoice: false,
      recording: false,
    });
  });

  it("本番でも既定は OFF（NODE_ENV や APP_ENV だけで発信が始まらない）", () => {
    const r = loadConfig(PROD);
    if (!r.ok) throw new Error("expected ok");
    expect(r.value.features.outboundCalls).toBe(false);
  });

  it("自動発信・AI 音声は、発信そのものが ON でなければ ON にできない", () => {
    expect(issuesOf({ AUTO_DIAL_ENABLED: "true" })).toEqual(["AUTO_DIAL_ENABLED"]);
    expect(issuesOf({ AI_VOICE_ENABLED: "true" })).toEqual(["AI_VOICE_ENABLED"]);
    expect(loadConfig({ OUTBOUND_CALLS_ENABLED: "true", AUTO_DIAL_ENABLED: "true" }).ok).toBe(true);
  });

  it("本番で発信を ON にするなら、本物の電話プロバイダを明示する（mock のまま本番発信を装わない）", () => {
    expect(issuesOf({ ...PROD, OUTBOUND_CALLS_ENABLED: "true" })).toEqual(["TELEPHONY_PROVIDER"]);
  });

  it("不正な値は ON 扱いにしない", () => {
    expect(issuesOf({ OUTBOUND_CALLS_ENABLED: "yes" })).toEqual(["OUTBOUND_CALLS_ENABLED"]);
  });
});

describe("Secret", () => {
  it("redacts itself", () => {
    const s = new Secret("abc");
    expect(`${s}`).toBe("[REDACTED]");
    expect(JSON.stringify({ s })).toBe('{"s":"[REDACTED]"}');
  });
});

describe(".env.example", () => {
  it("is itself a valid local configuration (the documented example never drifts)", async () => {
    const { readFile } = await import("node:fs/promises");
    const text = await readFile(new URL("../../../.env.example", import.meta.url), "utf8");
    const env = Object.fromEntries(
      text
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l !== "" && !l.startsWith("#"))
        .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
    );
    const r = loadConfig(env);
    expect(r.ok ? [] : r.error).toEqual([]);
  });
});
