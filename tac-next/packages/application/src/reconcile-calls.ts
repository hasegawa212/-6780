import { advanceCallStatus } from "./call-status-update.js";
import type { Deps } from "./deps.js";
import type { ProviderCallCandidate, UncertainCallFinder, UserId } from "./ports.js";

/** これより新しい確定しない発信は照合しない（Webhook が遅れて届くのを待つ） */
export const RECONCILE_MIN_AGE_MS = 2 * 60 * 1000;
/** これだけ経っても一覧に無ければ、発信されなかったとみなす（create-call の同時通話数の除外と同じ 15 分） */
export const NOT_PLACED_AFTER_MS = 15 * 60 * 1000;
/** 自分とプロバイダの時計のずれ・処理の遅れの許容 */
const CLOCK_SKEW_MS = 2 * 60 * 1000;
/** 作成からこの時間内に作られたプロバイダの通話だけを候補にする */
const MATCH_WINDOW_MS = 10 * 60 * 1000;
const DEFAULT_BATCH = 50;

/** 監査ログの操作者（人ではなく照合の処理） */
const RECONCILER = "system:reconciler" as UserId;

export type ReconcileKind =
  /** 一覧に 1 件だけ見つかり、その ID を付けた */
  | "MATCHED"
  /** 十分な時間が経っても見つからず、発信されなかったとして FAILED にした */
  | "NOT_PLACED"
  /** まだ見つからない（時間が浅い） */
  | "PENDING"
  /** 候補が複数あり決められない */
  | "AMBIGUOUS"
  /** 一覧を引けなかった */
  | "ERROR";

export interface ReconcileResult {
  readonly results: readonly { readonly callId: string; readonly kind: ReconcileKind }[];
}

/**
 * 確定しない発信の照合（ADR-0016、IQA-08 の根本対策）。
 * 発信の応答が届かず REQUESTED のまま・プロバイダの ID も無い通話を、プロバイダの通話一覧と突き合わせる。
 * 判断できないとき（候補が複数・一覧が引けない・一覧を持たないプロバイダ）は何も変えない。
 * 番号はふさがったままになるが、二重発信は起きない（fail closed）。
 */
export class ReconcileUncertainCallsUseCase {
  constructor(
    private readonly deps: Deps,
    private readonly finder: UncertainCallFinder,
  ) {}

  async execute(opts: { limit?: number } = {}): Promise<ReconcileResult> {
    const { deps } = this;
    const provider = deps.telephony;
    if (!provider.findCalls) return { results: [] };
    const now = deps.clock.now();
    const targets = await this.finder.list(
      new Date(now.getTime() - RECONCILE_MIN_AGE_MS),
      opts.limit ?? DEFAULT_BATCH,
    );
    const results: { callId: string; kind: ReconcileKind }[] = [];
    for (const target of targets) {
      const call = await deps.calls.get(target.organizationId, target.callId);
      if (call?.status !== "REQUESTED" || call.providerCallId !== undefined) continue;

      let listed: readonly ProviderCallCandidate[];
      try {
        listed = await provider.findCalls({
          to: call.to,
          from: call.from,
          createdAfter: new Date(call.createdAt.getTime() - CLOCK_SKEW_MS),
        });
      } catch {
        results.push({ callId: call.id, kind: "ERROR" });
        continue;
      }
      const latest = call.createdAt.getTime() + MATCH_WINDOW_MS;
      const candidates: ProviderCallCandidate[] = [];
      for (const c of listed) {
        if (c.createdAt.getTime() > latest) continue;
        if (c.createdAt.getTime() < call.createdAt.getTime() - CLOCK_SKEW_MS) continue;
        // すでに別の通話に付いている ID は、その通話のもの（同じ番号に別の組織・別の発信が掛けた）
        const owner = await deps.callLocator.locate(provider.name, c.providerCallId, undefined);
        if (owner && owner.callId !== call.id) continue;
        candidates.push(c);
      }

      if (candidates.length > 1) {
        results.push({ callId: call.id, kind: "AMBIGUOUS" });
        continue;
      }
      const [match] = candidates;
      if (match) {
        await deps.calls.attachProvider(
          call.organizationId,
          call.id,
          provider.name,
          match.providerCallId,
        );
        const current = await deps.calls.get(call.organizationId, call.id);
        if (current?.providerCallId !== match.providerCallId) {
          // 照合の間に Webhook が別の ID を付けた。そちらが正しいので何もしない
          continue;
        }
        const advanced = await advanceCallStatus(
          deps.calls,
          call.organizationId,
          call.id,
          match.status ?? "DIALING",
        );
        await this.audit(call.organizationId, call.id, now, {
          result: "MATCHED",
          providerCallId: match.providerCallId,
          status: advanced.call?.status,
        });
        if (advanced.applied && advanced.call) {
          await deps.events.publish({
            type: "CallStatusChanged",
            version: 1,
            organizationId: call.organizationId,
            occurredAt: now,
            payload: { callId: call.id, status: advanced.call.status, reconciled: true },
          });
        }
        results.push({ callId: call.id, kind: "MATCHED" });
        continue;
      }

      if (now.getTime() - call.createdAt.getTime() < NOT_PLACED_AFTER_MS) {
        results.push({ callId: call.id, kind: "PENDING" });
        continue;
      }
      const failed = await advanceCallStatus(deps.calls, call.organizationId, call.id, "FAILED");
      if (!failed.applied) continue;
      await this.audit(call.organizationId, call.id, now, { result: "NOT_PLACED" });
      await deps.events.publish({
        type: "CallFailed",
        version: 1,
        organizationId: call.organizationId,
        occurredAt: now,
        payload: { callId: call.id, stage: "reconcile" },
      });
      results.push({ callId: call.id, kind: "NOT_PLACED" });
    }
    return { results };
  }

  private audit(
    organizationId: Parameters<Deps["audit"]["append"]>[0]["organizationId"],
    callId: string,
    at: Date,
    after: Record<string, unknown>,
  ) {
    return this.deps.audit.append({
      organizationId,
      actorId: RECONCILER,
      action: "call.reconciled",
      resource: `call:${callId}`,
      at,
      before: { status: "REQUESTED" },
      after,
    });
  }
}
