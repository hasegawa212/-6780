import { randomUUID } from "node:crypto";
import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import type { ScryptPasswordHasher } from "./security.js";

export const DEMO_OPERATOR_EMAIL = "operator@example.test";

/**
 * local / test 用のデモデータ（架空の組織・担当者・リード）。config が staging / production では DEV_SEED_PASSWORD を拒否する。
 * 担当者が既にいれば何もしない（何度起動しても重複しない）。テーブルの所有者の接続で書き込む（組織の作成はアプリのロールに許していない）。
 */
export async function seedDemo(
  database: Database,
  password: string,
  hasher: ScryptPasswordHasher,
): Promise<boolean> {
  const { db } = database;
  const existing = await db.execute<{ id: string }>(
    sql`select id from users where email = ${DEMO_OPERATOR_EMAIL}`,
  );
  if (existing.rows.length > 0) return false;

  const org = randomUUID();
  const campaign = randomUUID();
  const user = randomUUID();
  // デモでは時間帯で止まらないよう終日・毎日にする（実際のキャンペーンの時間帯の判定は変えていない）
  const window = {
    timeZone: "Asia/Tokyo",
    startMinute: 0,
    endMinute: 24 * 60,
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    holidays: [],
  };
  await db.transaction(async (tx) => {
    await tx.execute(sql`
      insert into organizations (id, company_name, max_concurrent_calls)
      values (${org}, 'デモ不動産株式会社', 5)`);
    await tx.execute(sql`
      insert into campaigns (id, organization_id, product, caller_id_e164, calling_window,
        allowed_country_codes, daily_cap, per_number_daily_limit, max_attempts)
      values (${campaign}, ${org}, '新築戸建て', '+81300000000', ${JSON.stringify(window)}::jsonb,
        ${sql.raw("array['81']")}, 100, 3, 3)`);
    const leads = ["青木 一郎", "伊藤 花子", "上田 次郎", "江藤 三郎", "大野 さくら"];
    for (const [i, name] of leads.entries()) {
      await tx.execute(sql`
        insert into contacts (id, organization_id, display_name, phone_e164, time_zone)
        values (${randomUUID()}, ${org}, ${`${name}（架空）`}, ${`+81900000000${i + 1}`}, 'Asia/Tokyo')`);
    }
    await tx.execute(sql`
      insert into users (id, email, display_name, password_hash)
      values (${user}, ${DEMO_OPERATOR_EMAIL}, 'デモ担当', ${await hasher.hash(password)})`);
    await tx.execute(sql`
      insert into memberships (organization_id, user_id, role) values (${org}, ${user}, 'OPERATOR')`);
  });
  return true;
}
