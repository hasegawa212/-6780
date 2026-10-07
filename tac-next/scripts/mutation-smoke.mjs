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
const API = "apps/api/src";
const T = "packages/telephony/src";

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
  // Phase 3・7・8（API・認証・Webhook）
  [
    `${API}/app.ts`,
    "if (!csrf || !safeEqual(auth.tokens.hash(csrf), session.csrfHash)) {",
    "if (false) {",
    "CSRF トークンを検査しない",
  ],
  [
    `${API}/app.ts`,
    'if (!hasRole(session.role, min)) throw fail(403, "FORBIDDEN");',
    "",
    "ロールを検査しない（閲覧者が発信できる）",
  ],
  [
    `${API}/app.ts`,
    "if (!secret || !verifyMockWebhook(secret, raw, signature, deps.clock.now())) {",
    "if (false) {",
    "Webhook の署名を検証しない",
  ],
  [
    `${T}/index.ts`,
    "if (Math.abs(now.getTime() / 1000 - t) > SIGNATURE_TOLERANCE_SECONDS) return false;",
    "",
    "古い署名の Webhook を受け付ける（再送攻撃）",
  ],
  [
    "packages/config/src/index.ts",
    '(c.APP_ENV === "local" || c.APP_ENV === "test") && c.TELEPHONY_PROVIDER === "mock",',
    'c.TELEPHONY_PROVIDER === "mock",',
    "本番で mock の Webhook の受け口を開く",
  ],
  [
    `${A}/auth.ts`,
    'if (!membership) return err({ code: "ORGANIZATION_NOT_ALLOWED" });',
    "if (!membership) membership = candidate.memberships[0];",
    "所属していない組織でログインできる",
  ],
  [
    `${A}/call-status-update.ts`,
    "if (!next.applied) return { call: current, applied: false };",
    "",
    "重複・後戻りの Webhook を反映済みとして扱う",
  ],
  [
    `${A}/create-call.ts`,
    "    const advanced = await advanceCallStatus(\n",
    "    await deps.calls.update({ ...call, provider: placed.provider, providerCallId: placed.providerCallId, status: placed.status });\n    const advanced = await advanceCallStatus(\n",
    "発信の応答で、先に届いた Webhook の状態を上書きする",
  ],
  [
    `${DB}/src/repositories.ts`,
    "eq(t.calls.status, expected),",
    "",
    "状態の compare-and-set が期待値を見ない",
  ],
  [
    `${DB}/migrations/0003_auth_and_webhooks.sql`,
    "      where webhook_events.processed_at is null\n",
    "      where true\n",
    "処理済みの Webhook をもう一度処理する",
  ],
  [
    // 0005 が locate_provider_call を置き換えたので、変異は 0005 に入れる（0003 を壊しても実行されない）
    `${DB}/migrations/0005_independent_qa_fixes.sql`,
    "        and (c.provider is null or c.provider = p_provider)\n",
    "\n",
    "別のプロバイダの Webhook で通話を動かせる",
  ],
  [
    `${DB}/migrations/0003_auth_and_webhooks.sql`,
    "grant select, insert on call_events to tac_app;",
    "grant select, insert on call_events to tac_app;\n--> statement-breakpoint\ngrant select on users, sessions to tac_app;",
    "アプリのロールがパスワードのハッシュ・セッションを直接読める",
  ],
  [
    `${DB}/migrations/0003_auth_and_webhooks.sql`,
    "where s.id_hash = p_id_hash and s.revoked_at is null and s.expires_at > p_now",
    "where s.id_hash = p_id_hash and s.revoked_at is null",
    "期限切れのセッションを使える",
  ],
  // Phase 9（画面の表示ロジック・読み取り API）
  [
    `${W}/call-view.ts`,
    '  return "UNKNOWN";\n}\n\n/**\n * `POST /v1/calls`',
    '  return "NONE";\n}\n\n/**\n * `POST /v1/calls`',
    "抑止の状態が分からないのに発信ボタンを出す",
  ],
  [
    `${W}/call-view.ts`,
    'if (httpStatus < 500 || httpStatus === 502) return "CONFIRMED";',
    'return "CONFIRMED";',
    "発信されたか不明なのに新しい冪等キーで再送できる",
  ],
  [
    `${API}/app.ts`,
    'const suppression = check.unavailable ? "UNKNOWN" : check.allowed ? "NONE" : "SUPPRESSED";',
    'const suppression = check.allowed || check.unavailable ? "NONE" : "SUPPRESSED";',
    "抑止の照会に失敗した相手を「発信可」と画面に返す",
  ],
  // ログイン試行の制限・ユーザー作成
  [
    `${A}/auth.ts`,
    "    if (emailLocked) return locked(emailLocked);\n",
    "\n",
    "ロック中でもパスワードを照合する（総当たりを止めない）",
  ],
  [
    // IQA-10 で照合の前に予約（＝失敗として数える）ようになり、元の「failed() を呼ばない」変異は等価になった。
    // 代わりに「失敗した試行の記録を消す」変異にする（存在しないアドレスの試行も含め、失敗が数えられない）
    `${A}/auth.ts`,
    "const failed = async () => INVALID;",
    "const failed = async () => {\n      await deps.throttle.reset(emailKey);\n      return INVALID;\n    };",
    "失敗した試行を数えない（存在しないアドレスを含む。アカウントの有無が漏れる）",
  ],
  [
    `${DB}/migrations/0004_login_throttle.sql`,
    "where key = any(p_keys) and locked_until > p_now",
    "where key = any(p_keys)",
    "解除時刻を過ぎたロックがいつまでも効く",
  ],
  [`${API}/app.ts`, "...(clientIp ? { clientIp } : {}),", "", "IP 単位の制限を効かせていない"],
  [
    `${API}/admin.ts`,
    'if (validateNewPassword(input.password, email).length > 0) throw new AdminError("WEAK_PASSWORD");',
    "",
    "弱いパスワードのユーザーを作れる",
  ],
  // ---- 独立 QA（2026-10-06）の修正 ----
  [
    `${A}/record-outcome.ts`,
    'if (existing.code !== code && suppressionOf(code) !== "NONE") {',
    "if (false) {",
    "先に別の結果を記録した通話では、拒否を抑止にできない（IQA-01）",
  ],
  [
    `${A}/create-call.ts`,
    "idempotencyKey: call.id,",
    "idempotencyKey: key,",
    "組織をまたいで同じ冪等キーの発信がまとめられる（IQA-02）",
  ],
  [
    `${A}/create-call.ts`,
    "if (!(e instanceof ProviderRejectedError)) {",
    "if (e instanceof ProviderTimeoutError) {",
    "発信されたか分からない失敗を FAILED にして二重発信を許す（IQA-03）",
  ],
  [
    `${A}/create-call.ts`,
    '...(!campaignNow || campaignNow.paused ? ["CAMPAIGN_PAUSED"] : []),',
    "",
    "発信直前にキャンペーンの一時停止を見ない（IQA-04b）",
  ],
  [
    `${A}/provider-events.ts`,
    'if (result.kind === "UNKNOWN_CALL") {',
    "if (false) {",
    "特定できなかった Webhook を処理済みにして、再送を捨てる（IQA-11）",
  ],
  [
    `${A}/testing/in-memory.ts`,
    '!(r.status === "REQUESTED" && r.createdAt.getTime() < staleRequestedBefore.getTime()),',
    "true,",
    "確定しない発信が同時通話数の枠を永久に占有する（IQA-08）",
  ],
  [
    `${DB}/migrations/0005_independent_qa_fixes.sql`,
    "        and (c.provider_call_id is null or p_provider_call_id is null\n             or c.provider_call_id = p_provider_call_id))",
    ")",
    "記録と違うプロバイダの通話 ID の Webhook で状態が動く（IQA-05）",
  ],
  // 0005 の FOR UPDATE（ログイン試行の予約の直列化）は、同時実行が要るので PGlite では検出できない。
  // 実 PostgreSQL のテスト（test-postgres/iqa-login-throttle.test.ts、CI で必須）が検出することを確認済み。
  [
    `${A}/auth.ts`,
    "const emailLocked = await deps.throttle.reserve(emailKey, EMAIL_THROTTLE, now);",
    "const emailLocked = undefined;",
    "照合の前に試行の枠を予約しない（IQA-10）",
  ],
  [
    `${D}/phone.ts`,
    "if (trunk) international = trunk + international.slice(trunk.length + 1);",
    "",
    "+81 (0)90… を別の番号として扱い、抑止がすり抜ける（IQA-09）",
  ],
  // ---- Codex のレビュー（PR #137） ----
  [
    `${A}/record-outcome.ts`,
    'if (winner && winner.code !== code && suppressionOf(code) !== "NONE") {',
    "if (false) {",
    "同時に送られた拒否が競り負けると抑止にならない（Codex P1）",
  ],
  [
    `${API}/app.ts`,
    "        PROVIDER_UNCERTAIN: 504,\n",
    "",
    "発信されたか分からない失敗を 422 で返し、画面が新しいキーで掛け直す（Codex P1）",
  ],
  [
    `${A}/testing/in-memory.ts`,
    "const undoLock = row.lockedUntil !== undefined && row.lockedUntil > now && row.failures === 0;",
    "const undoLock = false;",
    "予約を戻してもロックが残る（Codex P2）",
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
