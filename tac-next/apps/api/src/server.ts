import { randomBytes } from "node:crypto";
import type { AddressInfo } from "node:net";
import { serve } from "@hono/node-server";
import { getConnInfo } from "@hono/node-server/conninfo";
import {
  type BudgetService,
  type Deps,
  type EventPublisher,
  ReconcileUncertainCallsUseCase,
} from "@tac/application";
import { loadConfig } from "@tac/config";
import {
  createNodePostgresDatabase,
  createPgAuthStores,
  createPgDeps,
  createPgliteDatabase,
  type Database,
  migrate,
  PgUncertainCallFinder,
  pendingMigrations,
  TenantScope,
  UuidIds,
} from "@tac/db";
import type { E164 } from "@tac/domain";
import {
  createTelephonyProvider,
  MOCK_SIGNATURE_HEADER,
  MockTelephonyProvider,
  signMockWebhook,
} from "@tac/telephony";
import { createApp } from "./app.js";
import { seedDemo } from "./dev-seed.js";
import { createReconcileLoop } from "./reconciler.js";
import { HmacTokens, ScryptPasswordHasher } from "./security.js";

const RECONCILE_INTERVAL_MS = 60_000;

/** イベントの配信先はまだ無い（outbox は Phase 6・15）。受け取って捨てる */
const discardEvents: EventPublisher = { publish: async () => {} };
/** 予算は未設定（null = 予算なし。ADR-0009 で再検討） */
const noBudget: BudgetService = { remaining: async () => null };

export interface RunningServer {
  readonly url: string;
  close(): Promise<void>;
}

/**
 * 設定を検証し、DB・電話プロバイダ・HTTP を組み立てて起動する。
 * - local / test：DATABASE_URL が無ければメモリ上の PGlite。マイグレーションは起動時に適用する
 * - staging / production：マイグレーションは自動で適用しない。未適用があれば起動しない（承認つきで別に実行する）
 */
export async function startServer(
  env: Readonly<Record<string, string | undefined>>,
  opts: {
    log?: (message: string) => void;
    database?: Database;
    /** テスト用：0 で空いているポートを使う（設定の PORT より優先） */
    port?: number;
  } = {},
): Promise<RunningServer> {
  const log = opts.log ?? ((m: string) => console.log(m));
  const loaded = loadConfig(env);
  if (!loaded.ok) {
    // 項目名と理由だけ（入力値は出さない）
    throw new Error(
      `invalid configuration: ${loaded.error.map((i) => `${i.path}: ${i.message}`).join("; ")}`,
    );
  }
  const config = loaded.value;
  const deployed = config.appEnv === "staging" || config.appEnv === "production";

  const ownsDatabase = opts.database === undefined;
  const database =
    opts.database ??
    (config.databaseUrl
      ? createNodePostgresDatabase(config.databaseUrl.reveal())
      : await createPgliteDatabase());
  try {
    if (deployed) {
      const pending = await pendingMigrations(database.db);
      if (pending.length > 0) throw new Error(`pending migrations: ${pending.join(", ")}`);
    } else {
      await migrate(database.db);
    }
    const passwords = new ScryptPasswordHasher();
    if (config.devSeedPassword) {
      if (await seedDemo(database, config.devSeedPassword.reveal(), passwords)) {
        log("demo data seeded (operator@example.test)");
      }
    }

    const sessionSecret =
      config.sessionSecret?.reveal() ??
      // local / test だけ（staging / production では config が SESSION_SECRET を必須にしている）
      randomBytes(32).toString("base64url");
    const scope = new TenantScope(database.db);
    const clock = { now: () => new Date() };
    const twilio = config.telephony.twilio;
    const twilioOptions =
      config.telephony.provider === "twilio" && twilio && config.publicBaseUrl
        ? {
            accountSid: twilio.accountSid,
            authToken: twilio.authToken.reveal(),
            agentNumber: twilio.agentNumber as E164,
            publicBaseUrl: config.publicBaseUrl,
            ringTimeoutSeconds: twilio.ringTimeoutSeconds,
            timeLimitSeconds: twilio.timeLimitSeconds,
          }
        : undefined;
    const telephony = createTelephonyProvider({
      appEnv: config.appEnv,
      provider: config.telephony.provider,
      twilio: twilioOptions,
    });
    const deps: Deps = {
      ...createPgDeps(scope),
      clock,
      ids: new UuidIds(),
      events: discardEvents,
      telephony,
      budget: noBudget,
      features: { outboundCalls: config.features.outboundCalls, aiVoice: config.features.aiVoice },
    };
    const app = createApp({
      deps,
      auth: {
        ...createPgAuthStores(scope),
        clock,
        passwords,
        tokens: new HmacTokens(sessionSecret),
        audit: deps.audit,
      },
      cookieSecure: deployed,
      clientIp: (c) => {
        const header = config.trustedClientIpHeader;
        if (header) return c.req.header(header)?.trim() || undefined;
        try {
          return getConnInfo(c).remote.address;
        } catch {
          return undefined;
        }
      },
      mockWebhooks: {
        enabled: config.telephony.mockWebhooksEnabled,
        secret: config.telephony.mockWebhookSecret?.reveal(),
        verifySignatures: config.safety.verifyWebhookSignatures,
      },
      ...(twilioOptions
        ? {
            twilioWebhooks: {
              authToken: twilioOptions.authToken,
              publicBaseUrl: twilioOptions.publicBaseUrl,
            },
          }
        : {}),
    });

    const server = serve({ fetch: app.fetch, port: opts.port ?? config.port });
    await new Promise<void>((resolve, reject) => {
      server.once("listening", () => resolve());
      server.once("error", reject);
    });
    const { port } = server.address() as AddressInfo;
    log(
      `tac-next api listening on :${port} (env=${config.appEnv}, telephony=${config.telephony.provider}, outbound=${config.features.outboundCalls})`,
    );
    const url = `http://127.0.0.1:${port}`;
    const pump = startMockDelivery(telephony, url, config.telephony.mockWebhookSecret?.reveal(), {
      enabled: config.telephony.mockWebhooksEnabled,
      log,
    });
    // 確定しない発信の照合（ADR-0016）。通話一覧を引けるプロバイダ（Twilio）のときだけ 1 分ごとに回す
    let reconcileTimer: NodeJS.Timeout | undefined;
    if (telephony.findCalls) {
      const finder = new PgUncertainCallFinder(scope);
      const loop = createReconcileLoop(
        () => new ReconcileUncertainCallsUseCase(deps, finder).execute(),
        log,
      );
      reconcileTimer = setInterval(() => void loop.tick(), RECONCILE_INTERVAL_MS);
      reconcileTimer.unref();
    }
    return {
      url,
      close: async () => {
        if (pump) clearInterval(pump);
        if (reconcileTimer) clearInterval(reconcileTimer);
        await new Promise<void>((resolve) => server.close(() => resolve()));
        if (ownsDatabase) await database.close();
      },
    };
  } catch (e) {
    if (ownsDatabase) await database.close();
    throw e;
  }
}

/**
 * local / test：シミュレーターのイベントを、時刻が来たら自分の Webhook の受け口へ署名つきで届ける
 * （画面で通話の状態が進むのを確かめるため。本物の電話はかけない）。staging / production では動かない。
 */
function startMockDelivery(
  telephony: unknown,
  baseUrl: string,
  secret: string | undefined,
  opts: { enabled: boolean; log: (m: string) => void },
): NodeJS.Timeout | undefined {
  if (!opts.enabled || !secret || !(telephony instanceof MockTelephonyProvider)) return undefined;
  const timer = setInterval(() => {
    for (const event of telephony.takeDueEvents(new Date())) {
      const body = JSON.stringify(event);
      void fetch(`${baseUrl}/v1/webhooks/mock`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          [MOCK_SIGNATURE_HEADER]: signMockWebhook(secret, body, new Date()),
        },
        body,
      }).catch((e: unknown) => opts.log(`mock webhook delivery failed: ${String(e)}`));
    }
  }, 250);
  timer.unref();
  return timer;
}
