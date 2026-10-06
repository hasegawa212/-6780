import { createNodePostgresDatabase, pendingMigrations } from "@tac/db";
import { AdminError, createUser } from "../admin.js";
import { ScryptPasswordHasher } from "../security.js";
import { CREATE_USER_USAGE, parseCreateUserArgs } from "./create-user-args.js";

// 運用 CLI：初期管理者・ユーザーの作成。DB の所有者（マイグレーションと同じ）の接続文字列で実行する
const args = parseCreateUserArgs(process.argv.slice(2));
if (!args.ok) {
  console.error(`${args.error}\n\n${CREATE_USER_USAGE}`);
  process.exit(2);
}
const url = process.env.DATABASE_URL;
if (!url) {
  console.error("DATABASE_URL が必要です");
  process.exit(2);
}

async function readPassword(): Promise<string> {
  const fromEnv = process.env.TAC_NEW_USER_PASSWORD;
  if (fromEnv) return fromEnv;
  if (process.stdin.isTTY) {
    throw new AdminError("PASSWORD_REQUIRED（TAC_NEW_USER_PASSWORD か標準入力で渡してください）");
  }
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks)
    .toString("utf8")
    .replace(/\r?\n$/, "");
}

const database = createNodePostgresDatabase(url, 2);
try {
  const pending = await pendingMigrations(database.db);
  if (pending.length > 0) throw new AdminError(`PENDING_MIGRATIONS: ${pending.join(", ")}`);
  const created = await createUser(database, new ScryptPasswordHasher(), {
    ...args.value,
    password: await readPassword(),
  });
  // パスワードは出力しない
  console.log(JSON.stringify(created));
} catch (e) {
  console.error(e instanceof AdminError ? `作成できませんでした: ${e.code}` : e);
  process.exitCode = 1;
} finally {
  await database.close();
}
