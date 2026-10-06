import { err, ok, type Result } from "@tac/domain";
import { z } from "zod";

/** 文字列化・JSON 化・console.log のどれでも中身を出さないシークレット。 */
export class Secret {
  readonly #value: string;
  constructor(value: string) {
    this.#value = value;
  }
  reveal(): string {
    return this.#value;
  }
  toString(): string {
    return "[REDACTED]";
  }
  toJSON(): string {
    return "[REDACTED]";
  }
  [Symbol.for("nodejs.util.inspect.custom")](): string {
    return "[REDACTED]";
  }
}

export type AppEnv = "local" | "test" | "staging" | "production";
export type TelephonyProviderName = "mock" | "twilio" | "openai-sip";

export interface AppConfig {
  readonly appEnv: AppEnv;
  readonly port: number;
  readonly logLevel: "debug" | "info" | "warn" | "error";
  readonly databaseUrl: Secret | undefined;
  readonly sessionSecret: Secret | undefined;
  /** local / test のデモ用シード（担当者 operator@example.test のパスワード）。staging / production では指定できない */
  readonly devSeedPassword: Secret | undefined;
  readonly telephony: {
    readonly provider: TelephonyProviderName;
    readonly twilio: { readonly accountSid: string; readonly authToken: Secret } | undefined;
    /** mock（シミュレーター）の Webhook の署名鍵。未設定なら mock の Webhook はすべて拒否する */
    readonly mockWebhookSecret: Secret | undefined;
    /** mock の Webhook の受け口を開けるか（local / test で mock のときだけ。偽の状態通知を本番に入れない） */
    readonly mockWebhooksEnabled: boolean;
  };
  /** 要求元の IP を取るヘッダー（例：fly-client-ip）。未設定なら接続の送信元アドレス。プロキシが必ず上書きするヘッダーだけを指定する */
  readonly trustedClientIpHeader: string | undefined;
  readonly safety: {
    readonly verifyWebhookSignatures: boolean;
    readonly enforceCallingWindow: boolean;
  };
  /** 危険な機能のゲート。すべて既定 OFF で、明示的に true にしたときだけ動く。 */
  readonly features: {
    readonly outboundCalls: boolean;
    readonly autoDial: boolean;
    readonly aiVoice: boolean;
    readonly recording: boolean;
  };
}

/** 入力値そのものは含めない（シークレットをエラー経由で漏らさないため）。 */
export interface ConfigIssue {
  readonly path: string;
  readonly message: string;
}

const bool = z.enum(["true", "false", "1", "0"]).transform((v) => v === "true" || v === "1");

const schema = z
  .object({
    APP_ENV: z.enum(["local", "test", "staging", "production"]).default("local"),
    PORT: z.coerce.number().int().min(1).max(65535).default(8080),
    LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
    TELEPHONY_PROVIDER: z.enum(["mock", "twilio", "openai-sip"]).default("mock"),
    DATABASE_URL: z
      .string()
      .regex(/^postgres(ql)?:\/\//)
      .optional(),
    SESSION_SECRET: z.string().min(32).optional(),
    TWILIO_ACCOUNT_SID: z
      .string()
      .regex(/^AC[0-9a-fA-F]{32}$/)
      .optional(),
    TWILIO_AUTH_TOKEN: z.string().min(16).optional(),
    MOCK_WEBHOOK_SECRET: z.string().min(32).optional(),
    DEV_SEED_PASSWORD: z.string().min(12).optional(),
    TRUSTED_CLIENT_IP_HEADER: z
      .string()
      .regex(/^[a-z0-9-]{1,64}$/)
      .optional(),
    VERIFY_WEBHOOK_SIGNATURES: bool.default(true),
    ENFORCE_CALLING_WINDOW: bool.default(true),
    OUTBOUND_CALLS_ENABLED: bool.default(false),
    AUTO_DIAL_ENABLED: bool.default(false),
    AI_VOICE_ENABLED: bool.default(false),
    RECORDING_ENABLED: bool.default(false),
  })
  .superRefine((c, ctx) => {
    const issue = (path: string, message: string) =>
      ctx.addIssue({ code: "custom", path: [path], message });
    const deployed = c.APP_ENV === "staging" || c.APP_ENV === "production";
    if (!deployed && c.TELEPHONY_PROVIDER !== "mock") {
      issue("TELEPHONY_PROVIDER", "local / test では mock 以外の電話プロバイダは使えません");
    }
    if (deployed && c.DEV_SEED_PASSWORD) {
      issue("DEV_SEED_PASSWORD", "デモ用のシードは local / test だけで使えます");
    }
    if (deployed && !c.DATABASE_URL) issue("DATABASE_URL", "staging / production では必須です");
    if (deployed && !c.SESSION_SECRET)
      issue("SESSION_SECRET", "staging / production では必須です（32 文字以上）");
    if (c.TELEPHONY_PROVIDER === "twilio") {
      if (!c.TWILIO_ACCOUNT_SID) issue("TWILIO_ACCOUNT_SID", "Twilio を使うときは必須です");
      if (!c.TWILIO_AUTH_TOKEN) issue("TWILIO_AUTH_TOKEN", "Twilio を使うときは必須です");
    }
    // 本番で安全装置を外すと、偽の Webhook や深夜の発信を防げなくなる
    if (c.APP_ENV === "production" && !c.VERIFY_WEBHOOK_SIGNATURES) {
      issue("VERIFY_WEBHOOK_SIGNATURES", "production では無効にできません");
    }
    if (c.APP_ENV === "production" && !c.ENFORCE_CALLING_WINDOW) {
      issue("ENFORCE_CALLING_WINDOW", "production では無効にできません");
    }
    // 自動発信・AI 音声は「発信そのもの」のゲートの内側にだけ置ける
    if (c.AUTO_DIAL_ENABLED && !c.OUTBOUND_CALLS_ENABLED) {
      issue("AUTO_DIAL_ENABLED", "OUTBOUND_CALLS_ENABLED=true のときだけ有効にできます");
    }
    if (c.AI_VOICE_ENABLED && !c.OUTBOUND_CALLS_ENABLED) {
      issue("AI_VOICE_ENABLED", "OUTBOUND_CALLS_ENABLED=true のときだけ有効にできます");
    }
    // staging / production で mock のまま発信 ON は設定ミス（発信したつもりで誰にもかかっていない）
    if (deployed && c.OUTBOUND_CALLS_ENABLED && c.TELEPHONY_PROVIDER === "mock") {
      issue(
        "TELEPHONY_PROVIDER",
        "staging / production で発信を有効にするときは mock 以外を指定してください",
      );
    }
  });

/** 環境変数を検証して設定を作る。空文字は未設定として扱う。 */
export function loadConfig(
  env: Readonly<Record<string, string | undefined>>,
): Result<AppConfig, ConfigIssue[]> {
  const cleaned = Object.fromEntries(
    Object.entries(env).filter(([, v]) => v !== undefined && v.trim() !== ""),
  );
  const parsed = schema.safeParse(cleaned);
  if (!parsed.success) {
    const seen = new Set<string>();
    const issues: ConfigIssue[] = [];
    for (const i of parsed.error.issues) {
      const path = i.path.join(".");
      if (seen.has(path)) continue;
      seen.add(path);
      issues.push({ path, message: i.code === "custom" ? i.message : `不正な値です（${i.code}）` });
    }
    return err(issues);
  }
  const c = parsed.data;
  return ok({
    appEnv: c.APP_ENV,
    port: c.PORT,
    logLevel: c.LOG_LEVEL,
    databaseUrl: c.DATABASE_URL ? new Secret(c.DATABASE_URL) : undefined,
    sessionSecret: c.SESSION_SECRET ? new Secret(c.SESSION_SECRET) : undefined,
    devSeedPassword: c.DEV_SEED_PASSWORD ? new Secret(c.DEV_SEED_PASSWORD) : undefined,
    trustedClientIpHeader: c.TRUSTED_CLIENT_IP_HEADER,
    telephony: {
      provider: c.TELEPHONY_PROVIDER,
      twilio:
        c.TWILIO_ACCOUNT_SID && c.TWILIO_AUTH_TOKEN
          ? { accountSid: c.TWILIO_ACCOUNT_SID, authToken: new Secret(c.TWILIO_AUTH_TOKEN) }
          : undefined,
      mockWebhookSecret: c.MOCK_WEBHOOK_SECRET ? new Secret(c.MOCK_WEBHOOK_SECRET) : undefined,
      mockWebhooksEnabled:
        (c.APP_ENV === "local" || c.APP_ENV === "test") && c.TELEPHONY_PROVIDER === "mock",
    },
    safety: {
      verifyWebhookSignatures: c.VERIFY_WEBHOOK_SIGNATURES,
      enforceCallingWindow: c.ENFORCE_CALLING_WINDOW,
    },
    features: {
      outboundCalls: c.OUTBOUND_CALLS_ENABLED,
      autoDial: c.AUTO_DIAL_ENABLED,
      aiVoice: c.AI_VOICE_ENABLED,
      recording: c.RECORDING_ENABLED,
    },
  });
}
