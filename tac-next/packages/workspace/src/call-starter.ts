export type CallStartSettlement = "CONFIRMED" | "UNKNOWN";

interface Entry {
  key: string;
  pending: boolean;
}

/**
 * 発信ボタンの二重送信対策（画面側）。相手ごとに冪等キーを1つ持つ。
 * - 応答待ちの間の連打は duplicate=true（送らない）
 * - 成否が確定したら（CONFIRMED）次の発信は新しいキー
 * - タイムアウト等で発信されたか不明（UNKNOWN）なら、同じキーを持ち続ける。
 *   再送しても同じキーなのでサーバー側の冪等性で二重発信にならない。
 * 最終的な保証はサーバー（Idempotency-Key と「番号ごとに回線上1件」の制約）。
 */
export class CallStarter {
  readonly #entries = new Map<string, Entry>();

  constructor(private readonly newKey: () => string) {}

  request(contactId: string): { key: string; duplicate: boolean } {
    const e = this.#entries.get(contactId);
    if (e?.pending) return { key: e.key, duplicate: true };
    if (e) {
      e.pending = true;
      return { key: e.key, duplicate: false };
    }
    const key = this.newKey();
    this.#entries.set(contactId, { key, pending: true });
    return { key, duplicate: false };
  }

  settle(contactId: string, outcome: CallStartSettlement): void {
    const e = this.#entries.get(contactId);
    if (!e) return;
    if (outcome === "CONFIRMED") this.#entries.delete(contactId);
    else e.pending = false;
  }
}
