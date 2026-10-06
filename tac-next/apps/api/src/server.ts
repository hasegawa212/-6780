import { randomBytes } from "node:crypto";
import type { AddressInfo } from "node:net";
import { serve } from "@hono/node-server";
import type { BudgetService, Deps, EventPublisher } from "@tac/application";
import { loadConfig } from "@tac/config";
import {
  createNodePostgresDatabase,
  createPgAuthStores,
  createPgDeps,
  createPgliteDatabase,
  type Database,
  migrate,
  pendingMigrations,
  TenantScope,
  UuidIds,
} from "@tac/db";
import { createTelephonyProvider } from "@tac/telephony";
import { createApp } from "./app.js";
import { HmacTokens, ScryptPasswordHasher } from "./security.js";

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

    const sessionSecret =
      config.sessionSecret?.reveal() ??
      // local / test だけ（staging / production では config が SESSION_SECRET を必須にしている）
      randomBytes(32).toString("base64url");
    const scope = new TenantScope(database.db);
    const clock = { now: () => new Date() };
    const deps: Deps = {
      ...createPgDeps(scope),
      clock,
      ids: new UuidIds(),
      events: discardEvents,
      telephony: createTelephonyProvider({
        appEnv: config.appEnv,
        provider: config.telephony.provider,
      }),
      budget: noBudget,
      features: { outboundCalls: config.features.outboundCalls, aiVoice: config.features.aiVoice },
    };
    const app = createApp({
      deps,
      auth: {
        ...createPgAuthStores(scope),
        clock,
        passwords: new ScryptPasswordHasher(),
        tokens: new HmacTokens(sessionSecret),
        audit: deps.audit,
      },
      cookieSecure: deployed,
      mockWebhooks: {
        enabled: config.telephony.mockWebhooksEnabled,
        secret: config.telephony.mockWebhookSecret?.reveal(),
        verifySignatures: config.safety.verifyWebhookSignatures,
      },
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
    return {
      url: `http://127.0.0.1:${port}`,
      close: async () => {
        await new Promise<void>((resolve) => server.close(() => resolve()));
        if (ownsDatabase) await database.close();
      },
    };
  } catch (e) {
    if (ownsDatabase) await database.close();
    throw e;
  }
}
