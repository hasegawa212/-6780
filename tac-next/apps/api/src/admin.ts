import { randomUUID } from "node:crypto";
import { normalizeEmail, ROLES, type Role, validateNewPassword } from "@tac/application";
import type { Database } from "@tac/db";
import { sql } from "drizzle-orm";
import { z } from "zod";
import type { ScryptPasswordHasher } from "./security.js";

/**
 * 運用の操作：初期管理者・ユーザーの作成（CLI `create-user` から使う）。
 * 組織・ユーザー・所属の作成はアプリのロール（tac_app）に許していないため、DB の所有者の接続で書き込む。
 * 招待・パスワード再設定の API は後続。
 */

export class AdminError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "AdminError";
  }
}

export interface CreateUserInput {
  readonly email: string;
  readonly displayName: string;
  readonly role: Role;
  readonly password: string;
  readonly organization:
    | { readonly id: string }
    | { readonly newName: string; readonly maxConcurrentCalls?: number };
}

export async function createUser(
  database: Database,
  hasher: ScryptPasswordHasher,
  input: CreateUserInput,
): Promise<{ userId: string; organizationId: string }> {
  const email = normalizeEmail(input.email);
  if (!z.email().max(320).safeParse(email).success) throw new AdminError("INVALID_EMAIL");
  if (!(ROLES as readonly string[]).includes(input.role)) throw new AdminError("INVALID_ROLE");
  const displayName = input.displayName.trim();
  if (displayName === "" || displayName.length > 100) throw new AdminError("INVALID_DISPLAY_NAME");
  if (validateNewPassword(input.password, email).length > 0) throw new AdminError("WEAK_PASSWORD");
  const passwordHash = await hasher.hash(input.password);

  return database.db.transaction(async (tx) => {
    const existing = await tx.execute(sql`select 1 from users where email = ${email}`);
    if (existing.rows.length > 0) throw new AdminError("USER_EXISTS");

    let organizationId: string;
    if ("id" in input.organization) {
      organizationId = input.organization.id;
      const org = z.uuid().safeParse(organizationId).success
        ? await tx.execute(sql`select 1 from organizations where id = ${organizationId}`)
        : { rows: [] };
      if (org.rows.length === 0) throw new AdminError("ORGANIZATION_NOT_FOUND");
    } else {
      const name = input.organization.newName.trim();
      if (name === "") throw new AdminError("INVALID_ORGANIZATION_NAME");
      organizationId = randomUUID();
      await tx.execute(sql`
        insert into organizations (id, company_name, max_concurrent_calls)
        values (${organizationId}, ${name}, ${input.organization.maxConcurrentCalls ?? 1})`);
    }

    const userId = randomUUID();
    await tx.execute(sql`
      insert into users (id, email, display_name, password_hash)
      values (${userId}, ${email}, ${displayName}, ${passwordHash})`);
    await tx.execute(sql`
      insert into memberships (organization_id, user_id, role)
      values (${organizationId}, ${userId}, ${input.role})`);
    await tx.execute(sql`
      insert into audit_logs (organization_id, actor_id, action, resource, after, at)
      values (${organizationId}, 'cli', 'admin.user_created', ${`user:${userId}`},
        ${JSON.stringify({ role: input.role })}::jsonb, now())`);
    return { userId, organizationId };
  });
}
