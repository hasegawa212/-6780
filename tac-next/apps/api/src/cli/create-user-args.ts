import { parseArgs } from "node:util";
import { ROLES, type Role } from "@tac/application";
import type { CreateUserInput } from "../admin.js";

export const CREATE_USER_USAGE = `使い方:
  DATABASE_URL=postgres://… TAC_NEW_USER_PASSWORD=… pnpm --filter @tac/api create-user \\
    --email owner@example.com --display-name "初期管理者" --role OWNER --new-org "組織名"
  既存の組織に追加するときは --new-org の代わりに --org-id <UUID>
  パスワードは環境変数 TAC_NEW_USER_PASSWORD か標準入力で渡す（引数では受け取らない）
  ロール: ${ROLES.join(" / ")}`;

export type ParsedArgs =
  | { ok: true; value: Omit<CreateUserInput, "password"> }
  | { ok: false; error: string };

export function parseCreateUserArgs(argv: readonly string[]): ParsedArgs {
  if (argv.some((a) => a === "--password" || a.startsWith("--password="))) {
    return {
      ok: false,
      error: "--password は使えません（TAC_NEW_USER_PASSWORD か標準入力で渡してください）",
    };
  }
  let values: Record<string, string | boolean | undefined>;
  try {
    values = parseArgs({
      args: [...argv],
      options: {
        email: { type: "string" },
        "display-name": { type: "string" },
        role: { type: "string" },
        "org-id": { type: "string" },
        "new-org": { type: "string" },
      },
      strict: true,
      allowPositionals: false,
    }).values;
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  const email = values.email;
  const displayName = values["display-name"];
  const role = values.role;
  if (typeof email !== "string") return { ok: false, error: "--email が必要です" };
  if (typeof displayName !== "string") return { ok: false, error: "--display-name が必要です" };
  if (typeof role !== "string" || !(ROLES as readonly string[]).includes(role)) {
    return { ok: false, error: `--role は ${ROLES.join(" / ")} のどれか` };
  }
  const orgId = values["org-id"];
  const newOrg = values["new-org"];
  if ((typeof orgId === "string") === (typeof newOrg === "string")) {
    return { ok: false, error: "--org-id か --new-org のどちらか一方を指定してください" };
  }
  return {
    ok: true,
    value: {
      email,
      displayName,
      role: role as Role,
      organization: typeof orgId === "string" ? { id: orgId } : { newName: String(newOrg) },
    },
  };
}
