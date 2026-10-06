#!/usr/bin/env node
// 重要な変異のスモークテスト。安全上重要な変異を1つずつ入れ、テストが「必ず落ちる」ことを確かめる。
// 生き残った変異 = その安全ルールはテストで守られていない、ということ。
// 移植元: hasegawa212/- sales-engagement-platform（Stryker が Vitest 5 で偽の生存を出したための代替。ADR-0009）。
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const D = "packages/domain/src";
const A = "packages/application/src";
const W = "packages/workspace/src";
const DB = "packages/db";

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
  // QA（2026-10-05）で塞いだ穴
  [
    `${A}/create-call.ts`,
    "if (e instanceof ActiveCallExistsError) {",
    "if (false) {",
    "別キーの同時発信で二重発信",
  ],
  [
    `${A}/create-call.ts`,
    "if (lateReasons.length > 0) {",
    "if (false) {",
    "判定後に入った DNC・停止を無視して発信",
  ],
  [
    `${A}/record-outcome.ts`,
    "if (e instanceof DuplicateOutcomeError) {",
    "if (false) {",
    "結果の同時送信で例外",
  ],
  [
    `${D}/calling-window.ts`,
    "    return false;\n  }\n  return (",
    "    return true;\n  }\n  return (",
    "不正なタイムゾーンを時間内扱い",
  ],
  [
    `${D}/utterance-safety.ts`,
    "if (isSoftDecline(utterance)) {",
    "if (false) {",
    "曖昧な断りを無視して説得を続ける",
  ],
  [
    `${W}/suppression-banner.ts`,
    'return i.suppression === "NONE" && !i.activeCall;',
    'return i.suppression !== "SAVED" && !i.activeCall;',
    "抑止の保存失敗・未確定でも発信ボタンを出す",
  ],
  [
    `${W}/call-starter.ts`,
    "if (e?.pending) return { key: e.key, duplicate: true };",
    "",
    "発信ボタンの連打で要求を重ねて送る",
  ],
  [
    `${W}/call-starter.ts`,
    'if (outcome === "CONFIRMED") this.#entries.delete(contactId);',
    "this.#entries.delete(contactId);",
    "発信されたか不明なのに新しい冪等キーで再送",
  ],
  [
    `${W}/outcome-flow.ts`,
    'if (preset && preset.requiresSuppression !== "NONE") {',
    "if (false) {",
    "拒否を選んでも抑止の確認が出ない",
  ],
  [
    `${W}/call-indicator.ts`,
    'if (i.conversation.controller === "HUMAN")',
    "if (false)",
    "引き継ぎ後も「AI が話しています」と出す",
  ],
  [
    "packages/config/src/index.ts",
    "OUTBOUND_CALLS_ENABLED: bool.default(false),",
    "OUTBOUND_CALLS_ENABLED: bool.default(true),",
    "発信ゲートが既定 ON",
  ],
  [
    `${D}/call-policy.ts`,
    'if (!f.deployment.outboundCallsEnabled) reasons.push("OUTBOUND_DISABLED_BY_CONFIG");',
    "",
    "設定で発信 OFF でも発信できる",
  ],
  [
    `${D}/call-policy.ts`,
    'if (!f.deployment.aiVoiceEnabled) reasons.push("AI_VOICE_DISABLED_BY_CONFIG");\n    else if',
    "if",
    "設定で AI 音声 OFF でも AI 音声で発信できる",
  ],
  [
    `${A}/create-call.ts`,
    "outboundCallsEnabled: deps.features.outboundCalls,",
    "outboundCallsEnabled: true,",
    "発信判定に設定のゲートを渡していない",
  ],
  [
    "packages/config/src/index.ts",
    "if (c.AUTO_DIAL_ENABLED && !c.OUTBOUND_CALLS_ENABLED) {",
    "if (false) {",
    "発信 OFF のまま自動発信を ON にできる",
  ],
  [
    "packages/config/src/index.ts",
    "if (c.AI_VOICE_ENABLED && !c.OUTBOUND_CALLS_ENABLED) {",
    "if (false) {",
    "発信 OFF のまま AI 音声を ON にできる",
  ],
  [
    "packages/config/src/index.ts",
    'if (deployed && c.OUTBOUND_CALLS_ENABLED && c.TELEPHONY_PROVIDER === "mock") {',
    "if (false) {",
    "staging / production で mock のまま発信 ON",
  ],
  // Phase 2（DB 層）
  [
    `${A}/create-call.ts`,
    "admitted = await deps.uow.runExclusive(cmd.organizationId, () =>",
    "admitted = await deps.uow.run(() =>",
    "上限の判定と保存を組織単位で直列化しない",
  ],
  [
    `${DB}/src/tenant.ts`,
    "await tx.execute(sql`set local role tac_app`);",
    "",
    "アプリのロールに切り替えず RLS が効かない",
  ],
  [
    `${DB}/migrations/0002_tenant_isolation.sql`,
    "alter table contacts enable row level security;",
    "select 1;",
    "連絡先の RLS が無効",
  ],
  [
    `${DB}/src/tenant.ts`,
    "select pg_advisory_xact_lock(hashtextextended(",
    "select abs(hashtextextended(",
    "組織ロックを取らない",
  ],
  [
    `${DB}/migrations/0001_core_schema.sql`,
    "create unique index calls_one_active_per_number_uq",
    "create index calls_one_active_per_number_uq",
    "回線上の通話の一意制約がない（二重発信）",
  ],
  [
    `${DB}/migrations/0002_tenant_isolation.sql`,
    "grant select, insert on suppression_entries to tac_app;",
    "grant select, insert, update, delete on suppression_entries to tac_app;",
    "アプリが抑止を削除・解除できる",
  ],
  [
    `${DB}/migrations/0002_tenant_isolation.sql`,
    "grant select, insert on audit_logs to tac_app;",
    "grant select, insert, update, delete on audit_logs to tac_app;",
    "監査ログを書き換えられる",
  ],
  [
    `${DB}/src/repositories.ts`,
    "return rows.length === 0;",
    "return true;",
    "保存された抑止を照会で無視",
  ],
  [
    `${DB}/src/repositories.ts`,
    "return row?.stopped !== false;",
    "return row?.stopped === true;",
    "全発信停止の行が無いと発信してしまう（fail open）",
  ],
];

// 変異は Critical Invariant Suite だけで検出できなければならない（全テストで偶然落ちるのでは足りない）
const run = () =>
  spawnSync(
    "pnpm",
    ["exec", "vitest", "run", "--config", "vitest.critical.config.ts", "--reporter=dot"],
    {
      encoding: "utf8",
    },
  );
// テストが実行できなかった（pnpm が無い・シグナルで終了等）のを「KILLED」と数えない
const ran = (r) => !r.error && r.status !== null && /Test Files/.test(`${r.stdout}${r.stderr}`);

const baseline = run();
if (!ran(baseline) || baseline.status !== 0) {
  console.error("ABORT    変異を入れる前の Critical Suite が実行できない、または失敗している");
  console.error(baseline.error ?? `${baseline.stdout}${baseline.stderr}`.slice(-2000));
  process.exit(2);
}

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
    const r = run();
    if (!ran(r)) {
      console.error(`ABORT    ${label}: テストを実行できなかった`, r.error ?? r.signal ?? "");
      process.exit(2);
    }
    const killed = r.status !== 0;
    console.log(`${killed ? "KILLED  " : "SURVIVED"} ${label}`);
    if (!killed) survived++;
  } finally {
    writeFileSync(file, original);
  }
}
console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} critical mutants killed`);
process.exit(survived === 0 ? 0 : 1);
