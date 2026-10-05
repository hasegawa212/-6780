#!/usr/bin/env node
// 重要な変異のスモークテスト。安全上重要な変異を1つずつ入れ、テストが「必ず落ちる」ことを確かめる。
// 生き残った変異 = その安全ルールはテストで守られていない、ということ。
// 移植元: hasegawa212/- sales-engagement-platform（Stryker が Vitest 5 で偽の生存を出したための代替。ADR-0009）。
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const D = "packages/domain/src";
const A = "packages/application/src";

const MUTANTS = [
  [
    `${A}/suppression-check.ts`,
    "allowed: answer === true",
    "allowed: answer !== false",
    "抑止の照会で真偽値以外を許可",
  ],
  [
    `${A}/suppression-check.ts`,
    "return { allowed: false, unavailable: true };",
    "return { allowed: true, unavailable: true };",
    "抑止の照会失敗で発信（fail open）",
  ],
  [
    `${A}/call-queue.ts`,
    ".allowed) continue;",
    ".unavailable) continue;",
    "キューが抑止中の相手を出す",
  ],
  // 入口の replay を消しても挿入時の重複検出で止まる（多層防御の等価変異）ため、内容の照合を壊す
  [
    `${A}/create-call.ts`,
    "if (existing.requestFingerprint !== fingerprint(cmd))",
    "if (false)",
    "同じ冪等キーを別の相手に使い回せる",
  ],
  [
    `${D}/call-policy.ts`,
    'if (f.suppressed) reasons.push("CONTACT_SUPPRESSED");',
    "",
    "抑止中でも発信",
  ],
  [
    `${D}/call-policy.ts`,
    'if (f.outboundStopped) reasons.push("OUTBOUND_STOPPED");',
    "",
    "全発信停止を無視",
  ],
  [
    `${D}/call-policy.ts`,
    'else if (!f.hasValidConsent) reasons.push("CONSENT_REQUIRED");',
    "",
    "同意なしで AI 発信",
  ],
  [
    `${D}/conversation.ts`,
    'DO_NOT_CALL: ["END_CONVERSATION", "ADD_SUPPRESSION", "AUDIT"],',
    'DO_NOT_CALL: ["END_CONVERSATION", "AUDIT"],',
    "拒否しても抑止されない",
  ],
  [
    `${D}/conversation.ts`,
    'DISCLOSURE: ["PERMISSION"],',
    'DISCLOSURE: ["PERMISSION", "QUALIFICATION"],',
    "名乗り・確認を飛ばして営業",
  ],
  [
    `${D}/conversation.ts`,
    'DO_NOT_CALL: ["COMPLETED"],',
    'DO_NOT_CALL: ["COMPLETED", "OBJECTION"],',
    "拒否後に説得へ戻る",
  ],
  [
    `${D}/conversation.ts`,
    'return state.controller === "AI" && ',
    "return ",
    "人が引き継いだ後も AI が話す",
  ],
  [
    `${D}/call-status.ts`,
    "if (isReachable(current, incoming)) return { status: incoming, applied: true };",
    "return { status: incoming, applied: true };",
    "古い Webhook で状態が後戻り",
  ],
  [
    `${D}/utterance-safety.ts`,
    "/電話(を)?(し|かけ)(て(こ|く)ない|ない)で/,",
    "",
    "「電話しないで」を取りこぼす",
  ],
  [
    `${D}/utterance-safety.ts`,
    'if (matches(DO_NOT_CALL, t)) found.push("DO_NOT_CALL");',
    'if (matches(DO_NOT_CALL, t)) found.push("COMPLAINT");',
    "明確な拒否を抑止にしない",
  ],
  [
    `${D}/tac-follow.ts`,
    '連絡停止: { nextAction: "DO_NOT_CONTACT", suppression: "CONTACT" },',
    '連絡停止: { nextAction: "DO_NOT_CONTACT", suppression: "NONE" },',
    "連絡停止が抑止にならない",
  ],
  [
    `${D}/outcome.ts`,
    'label: "拒否",\n    category: "NEGATIVE",\n    requiresFollowUp: false,\n    requiresSuppression: "CONTACT",',
    'label: "拒否",\n    category: "NEGATIVE",\n    requiresFollowUp: false,\n    requiresSuppression: "NONE",',
    "拒否ボタンが抑止にならない",
  ],
];

let survived = 0;
for (const [file, from, to, label] of MUTANTS) {
  const original = readFileSync(file, "utf8");
  if (!original.includes(from)) {
    console.error(`STALE    ${label}: ${file} にパターンがない（変異の定義を更新すること）`);
    survived++;
    continue;
  }
  writeFileSync(file, original.replace(from, to));
  try {
    const r = spawnSync("pnpm", ["exec", "vitest", "run", "--reporter=dot"], { encoding: "utf8" });
    const killed = r.status !== 0;
    console.log(`${killed ? "KILLED  " : "SURVIVED"} ${label}`);
    if (!killed) survived++;
  } finally {
    writeFileSync(file, original);
  }
}
console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} critical mutants killed`);
process.exit(survived === 0 ? 0 : 1);
