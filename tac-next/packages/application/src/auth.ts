import { err, ok, type Result } from "@tac/domain";
import type { AppError } from "./deps.js";
import type { AuditLog, Clock, OrganizationId, UserId } from "./ports.js";

/**
 * 認証・セッション・ロール（Phase 3）。
 * セッションの ID と CSRF トークンは呼び出し側（ブラウザ）にだけ渡し、保存するのはハッシュだけ。
 * organization_id とロールはセッションから決まる（リクエストの値を信用しない）。
 */

export const ROLES = ["VIEWER", "OPERATOR", "MANAGER", "ADMIN", "OWNER"] as const;
export type Role = (typeof ROLES)[number];

/** role が min 以上の権限を持つか（OWNER > ADMIN > MANAGER > OPERATOR > VIEWER） */
export const hasRole = (role: Role, min: Role): boolean =>
  ROLES.indexOf(role) >= ROLES.indexOf(min);

export const SESSION_TTL_MS = 12 * 60 * 60 * 1000;

export interface LoginCandidate {
  readonly userId: UserId;
  readonly displayName: string;
  readonly passwordHash: string;
  /** 所属（無効化されたユーザーは候補に出さない） */
  readonly memberships: readonly { readonly organizationId: OrganizationId; readonly role: Role }[];
}

export interface AuthDirectory {
  /** email は正規化（trim・小文字）済みで渡す */
  findForLogin(email: string): Promise<LoginCandidate | undefined>;
}

export interface ActiveSession {
  readonly userId: UserId;
  readonly organizationId: OrganizationId;
  /** 要求のたびに所属から引き直したロール */
  readonly role: Role;
  readonly displayName: string;
  readonly csrfHash: string;
  readonly expiresAt: Date;
}

export interface SessionStore {
  create(session: {
    idHash: string;
    userId: UserId;
    organizationId: OrganizationId;
    csrfHash: string;
    createdAt: Date;
    expiresAt: Date;
  }): Promise<void>;
  /** 期限内・未取り消し・所属が有効・ユーザーが有効なものだけ返す */
  resolve(idHash: string, now: Date): Promise<ActiveSession | undefined>;
  revoke(idHash: string, at: Date): Promise<void>;
}

export interface PasswordHasher {
  verify(password: string, hash: string): Promise<boolean>;
  /** 存在しないユーザーのときに、同じだけ時間をかける（有無を応答時間で漏らさない） */
  verifyDummy(password: string): Promise<void>;
}

/** 推測できない乱数のトークンと、その保存用のハッシュ */
export interface SecretTokens {
  newToken(): string;
  hash(token: string): string;
}

/** ログイン試行の制限の方針：windowMs の間に maxFailures 回失敗したら lockMs ロックする */
export interface ThrottlePolicy {
  readonly maxFailures: number;
  readonly windowMs: number;
  readonly lockMs: number;
}

const MINUTE = 60 * 1000;
/** メールアドレス単位（存在しないアドレスも同じに数える） */
export const EMAIL_THROTTLE: ThrottlePolicy = {
  maxFailures: 5,
  windowMs: 15 * MINUTE,
  lockMs: 15 * MINUTE,
};
/** IP 単位（多数のアドレスを順に試す攻撃） */
export const IP_THROTTLE: ThrottlePolicy = {
  maxFailures: 50,
  windowMs: 15 * MINUTE,
  lockMs: 15 * MINUTE,
};

/**
 * ログイン試行の記録（複数のサーバーで共有する。キーはハッシュで、メールアドレス・IP をそのまま残さない）。
 */
export interface LoginThrottle {
  /** いずれかのキーがロック中なら、最も遅い解除時刻 */
  lockedUntil(keys: readonly string[], now: Date): Promise<Date | undefined>;
  recordFailure(key: string, policy: ThrottlePolicy, now: Date): Promise<void>;
  /**
   * 照合の前に試行の枠を 1 回分、不可分に予約する（IQA-10）。ロック中なら解除時刻を返し、何も数えない。
   * 予約は失敗として数え、上限ちょうどの試行で以後をロックする。成功したら reset / refund で戻す。
   */
  reserve(key: string, policy: ThrottlePolicy, now: Date): Promise<Date | undefined>;
  /** 予約を 1 回分戻す（成功してもリセットしないキー用） */
  refund(key: string): Promise<void>;
  reset(key: string): Promise<void>;
}

export interface AuthDeps {
  readonly clock: Clock;
  readonly directory: AuthDirectory;
  readonly sessions: SessionStore;
  readonly passwords: PasswordHasher;
  readonly tokens: SecretTokens;
  readonly audit: AuditLog;
  /** 必須（総当たり対策を組み立て側が入れ忘れないように） */
  readonly throttle: LoginThrottle;
}

export interface LoginCommand {
  readonly email: string;
  readonly password: string;
  readonly organizationId?: string;
  /** 要求元の IP（分からなければ省略。メールアドレス単位の制限だけになる） */
  readonly clientIp?: string;
}

export interface LoginResult {
  readonly sessionToken: string;
  readonly csrfToken: string;
  readonly expiresAt: Date;
  readonly userId: UserId;
  readonly displayName: string;
  readonly organizationId: OrganizationId;
  readonly role: Role;
}

const INVALID = err({ code: "INVALID_CREDENTIALS" });

export const normalizeEmail = (email: string) => email.trim().toLowerCase();

export class LoginUseCase {
  constructor(private readonly deps: AuthDeps) {}

  async execute(cmd: LoginCommand): Promise<Result<LoginResult, AppError>> {
    const { deps } = this;
    const email = normalizeEmail(cmd.email);
    const emailKey = `email:${deps.tokens.hash(`login-email:${email}`)}`;
    const ipKey = cmd.clientIp ? `ip:${deps.tokens.hash(`login-ip:${cmd.clientIp}`)}` : undefined;

    // ロック中はパスワードを照合しない（照合の回数そのものを制限する）。
    // 「確認してから照合」だと同時の試行が上限を超えて照合されるので、照合の前に枠を不可分に予約する（IQA-10）
    const now = deps.clock.now();
    const locked = (until: Date) =>
      err({
        code: "LOGIN_LOCKED",
        retryAfterSeconds: Math.max(1, Math.ceil((until.getTime() - now.getTime()) / 1000)),
      });
    const emailLocked = await deps.throttle.reserve(emailKey, EMAIL_THROTTLE, now);
    if (emailLocked) return locked(emailLocked);
    if (ipKey) {
      const ipLocked = await deps.throttle.reserve(ipKey, IP_THROTTLE, now);
      if (ipLocked) {
        await deps.throttle.refund(emailKey); // 照合していない試行はアドレス側に数えない
        return locked(ipLocked);
      }
    }
    // 予約の時点で失敗として数えてある
    const failed = async () => INVALID;

    const candidate = await deps.directory.findForLogin(email);
    if (!candidate) {
      await deps.passwords.verifyDummy(cmd.password);
      return failed();
    }
    if (!(await deps.passwords.verify(cmd.password, candidate.passwordHash))) return failed();
    await deps.throttle.reset(emailKey);
    if (ipKey) await deps.throttle.refund(ipKey);
    // ここから先はパスワードが正しい本人だけが見る応答
    if (candidate.memberships.length === 0) return INVALID;
    let membership = candidate.memberships[0];
    if (cmd.organizationId !== undefined) {
      membership = candidate.memberships.find((m) => m.organizationId === cmd.organizationId);
      if (!membership) return err({ code: "ORGANIZATION_NOT_ALLOWED" });
    } else if (candidate.memberships.length > 1) {
      return err({ code: "ORGANIZATION_REQUIRED" });
    }
    if (!membership) return INVALID;

    const sessionToken = deps.tokens.newToken();
    const csrfToken = deps.tokens.newToken();
    const expiresAt = new Date(now.getTime() + SESSION_TTL_MS);
    await deps.sessions.create({
      idHash: deps.tokens.hash(sessionToken),
      userId: candidate.userId,
      organizationId: membership.organizationId,
      csrfHash: deps.tokens.hash(csrfToken),
      createdAt: now,
      expiresAt,
    });
    await deps.audit.append({
      organizationId: membership.organizationId,
      actorId: candidate.userId,
      action: "auth.login",
      resource: `user:${candidate.userId}`,
      at: now,
      after: { role: membership.role },
    });
    return ok({
      sessionToken,
      csrfToken,
      expiresAt,
      userId: candidate.userId,
      displayName: candidate.displayName,
      organizationId: membership.organizationId,
      role: membership.role,
    });
  }
}

export class ResolveSessionUseCase {
  constructor(private readonly deps: Pick<AuthDeps, "clock" | "sessions" | "tokens">) {}
  execute(sessionToken: string): Promise<ActiveSession | undefined> {
    return this.deps.sessions.resolve(this.deps.tokens.hash(sessionToken), this.deps.clock.now());
  }
}

export class LogoutUseCase {
  constructor(private readonly deps: Pick<AuthDeps, "clock" | "sessions" | "tokens">) {}
  execute(sessionToken: string): Promise<void> {
    return this.deps.sessions.revoke(this.deps.tokens.hash(sessionToken), this.deps.clock.now());
  }
}

export type PasswordIssue = "TOO_SHORT" | "TOO_LONG" | "SAME_AS_EMAIL";

/** 新しいパスワードの方針（NIST SP 800-63B に沿い、長さを重視して文字種の強制はしない） */
export function validateNewPassword(password: string, email: string): PasswordIssue[] {
  const issues: PasswordIssue[] = [];
  if (password.length < 12) issues.push("TOO_SHORT");
  if (password.length > 1024) issues.push("TOO_LONG");
  if (normalizeEmail(password) === normalizeEmail(email)) issues.push("SAME_AS_EMAIL");
  return issues;
}
