import { err, ok, type Result } from "./result.js";

declare const e164Brand: unique symbol;
/** 検証済みの E.164 番号（`+` と 8〜15 桁）。toE164 以外では作れない。 */
export type E164 = string & { readonly [e164Brand]: true };

export type PhoneError = "EMPTY" | "INVALID_CHARACTERS" | "NO_COUNTRY_CODE" | "INVALID_LENGTH";

// E.164 は国番号込みで最大 15 桁。8 桁未満は国番号だけ・桁の欠けた入力とみなす。
const MIN_DIGITS = 8;
const MAX_DIGITS = 15;
/**
 * 国内では先頭に 0（トランクプレフィックス）を付けるが、E.164 では付けない国。
 * `+81 (0)90…`・`+81 090…` のように国番号の後ろに 0 を残した表記を、同じ E.164 にそろえる（IQA-09）。
 * イタリア（39）のように 0 が番号の一部の国は入れない。
 */
const TRUNK_ZERO_COUNTRIES = ["81", "82", "86", "44", "49", "33", "61", "64"] as const;

/**
 * 入力された電話番号を E.164 に正規化する。
 * - `+…` は国番号付きとして記号だけ除く
 * - `00…` は国際プレフィックスとして `+` に置き換える
 * - `0…` は国内表記として先頭の 0 を落とし defaultCountryCode を付ける
 * - 国番号の後ろに残った国内の 0（`+81 (0)90…`）は落とす（TRUNK_ZERO_COUNTRIES）
 * 全角数字・全角記号（iPhone の日本語キーボード）も受け付ける。
 */
export function toE164(raw: string, defaultCountryCode = "81"): Result<E164, PhoneError> {
  const s = raw.normalize("NFKC").trim();
  if (s === "") return err("EMPTY");
  if (/[^\d\s\-().+]/.test(s) || s.indexOf("+") > 0) return err("INVALID_CHARACTERS");

  const digits = s.replace(/\D/g, "");
  let international: string;
  if (s.startsWith("+")) international = digits;
  else if (digits.startsWith("00")) international = digits.slice(2);
  else if (digits.startsWith("0")) international = defaultCountryCode + digits.slice(1);
  else return err("NO_COUNTRY_CODE");
  const trunk = TRUNK_ZERO_COUNTRIES.find(
    (cc) => international.startsWith(cc) && international[cc.length] === "0",
  );
  if (trunk) international = trunk + international.slice(trunk.length + 1);

  if (international.length < MIN_DIGITS || international.length > MAX_DIGITS) {
    return err("INVALID_LENGTH");
  }
  return ok(`+${international}` as E164);
}

/** 国番号の許可リストに入っているか。E.164 の国番号は接頭辞が重ならないので前方一致で判定できる。 */
export function isAllowedCountry(number: E164, allowedCountryCodes: readonly string[]): boolean {
  return allowedCountryCodes.some((cc) => number.startsWith(`+${cc}`));
}

/** ログ用のマスク表記（先頭 4 桁と末尾 4 桁だけ残す）。 */
export function maskE164(number: E164): string {
  const digits = number.slice(1);
  const hidden = Math.max(digits.length - 8, 1);
  return `+${digits.slice(0, 4)}${"*".repeat(hidden)}${digits.slice(4 + hidden)}`;
}
