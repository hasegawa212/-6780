import { createHmac, randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import type { PasswordHasher, SecretTokens } from "@tac/application";

/**
 * パスワードのハッシュ（scrypt）。既定は OWASP Password Storage Cheat Sheet の推奨
 * N=2^17・r=8・p=1。形式は `scrypt$<log2 N>$<r>$<p>$<salt>$<hash>`（base64url）。
 */
export class ScryptPasswordHasher implements PasswordHasher {
  private readonly logN: number;
  private dummy: Promise<string> | undefined;

  constructor(opts: { logN?: number } = {}) {
    this.logN = opts.logN ?? 17;
    if (!Number.isInteger(this.logN) || this.logN < 10 || this.logN > 20) {
      throw new Error("scrypt cost (logN) must be an integer between 10 and 20");
    }
  }

  async hash(password: string): Promise<string> {
    const salt = randomBytes(16);
    const key = await derive(password, salt, this.logN, 8, 1);
    return ["scrypt", this.logN, 8, 1, salt.toString("base64url"), key.toString("base64url")].join(
      "$",
    );
  }

  async verify(password: string, stored: string): Promise<boolean> {
    const parts = stored.split("$");
    if (parts.length !== 6 || parts[0] !== "scrypt") return false;
    const [logN, r, p] = parts.slice(1, 4).map(Number);
    if (!logN || !r || !p || logN < 10 || logN > 20 || r > 32 || p > 16) return false;
    const salt = Buffer.from(parts[4] ?? "", "base64url");
    const expected = Buffer.from(parts[5] ?? "", "base64url");
    if (salt.length < 16 || expected.length !== 32) return false;
    const actual = await derive(password, salt, logN, r, p);
    return timingSafeEqual(actual, expected);
  }

  async verifyDummy(password: string): Promise<void> {
    this.dummy ??= this.hash(randomBytes(16).toString("base64url"));
    await this.verify(password, await this.dummy);
  }
}

function derive(password: string, salt: Buffer, logN: number, r: number, p: number) {
  const N = 2 ** logN;
  return new Promise<Buffer>((resolve, reject) => {
    // maxmem は 128 * N * r * p バイトに余裕を持たせる（既定の 32MB では N=2^17 が動かない）
    scrypt(password, salt, 32, { N, r, p, maxmem: 256 * N * r * p }, (e, key) =>
      e ? reject(e) : resolve(key),
    );
  });
}

/**
 * セッション ID・CSRF トークン：256 bit の乱数（base64url）。保存するのは HMAC-SHA256(SESSION_SECRET, token)。
 * DB が漏れても、ハッシュからトークンは作れない。
 */
export class HmacTokens implements SecretTokens {
  constructor(private readonly secret: string) {
    if (secret.length < 32) throw new Error("session secret must be at least 32 characters");
  }
  newToken(): string {
    return randomBytes(32).toString("base64url");
  }
  hash(token: string): string {
    return createHmac("sha256", this.secret).update(token).digest("base64url");
  }
}

/** 文字列を定数時間で比較する（長さが違えば false） */
export function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** 応答・ログに出す電話番号のマスク：`+819000000001` → `+8190****0001` */
export function maskE164(e164: string): string {
  if (e164.length <= 9) return "****";
  return `${e164.slice(0, 5)}****${e164.slice(-4)}`;
}
