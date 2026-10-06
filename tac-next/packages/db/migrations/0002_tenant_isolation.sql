-- 0002: アプリのロール・権限・RLS（INV-2 テナント分離の DB 層、docs/DATABASE.md）
-- アプリは接続後、トランザクションごとに `SET LOCAL ROLE tac_app` と
-- `set_config('app.org_id', <組織>, true)` を行う（packages/db/src/tenant.ts）。
-- app.org_id が未設定・空なら NULL になり、どの行にも一致しない（fail closed）。
-- tac_app は NOLOGIN。本番の接続ユーザーを tac_app のメンバーにする（docs/DATABASE.md「運用」）。

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'tac_app') then
    create role tac_app nologin noinherit;
  end if;
end
$$;
--> statement-breakpoint
create function app_current_org() returns uuid
  language sql stable
  as $$ select nullif(current_setting('app.org_id', true), '')::uuid $$;
--> statement-breakpoint
grant usage on schema public to tac_app;
--> statement-breakpoint
grant execute on function app_current_org() to tac_app;
--> statement-breakpoint
-- 組織の作成・変更は管理者の操作（Phase 3）。アプリは読むだけ
grant select on organizations to tac_app;
--> statement-breakpoint
grant select, insert, update on contacts, campaigns, calls, follow_ups, consents to tac_app;
--> statement-breakpoint
-- 結果は書き直さない（通話ごとに1件、記録したら確定）
grant select, insert on outcomes to tac_app;
--> statement-breakpoint
-- 抑止は追加だけ。削除・解除（UPDATE）はアプリに与えない（INV-1。解除は Phase 5 で理由つきの専用経路）
grant select, insert on suppression_entries to tac_app;
--> statement-breakpoint
-- 監査ログは追記専用
grant select, insert on audit_logs to tac_app;
--> statement-breakpoint
-- 全発信停止はアプリから読むだけ（止める・再開するのは運用の操作）
grant select on system_controls to tac_app;
--> statement-breakpoint
alter table organizations enable row level security;
--> statement-breakpoint
alter table organizations force row level security;
--> statement-breakpoint
create policy tenant_isolation on organizations
  using (id = app_current_org()) with check (id = app_current_org());
--> statement-breakpoint
alter table contacts enable row level security;
--> statement-breakpoint
alter table contacts force row level security;
--> statement-breakpoint
create policy tenant_isolation on contacts
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table campaigns enable row level security;
--> statement-breakpoint
alter table campaigns force row level security;
--> statement-breakpoint
create policy tenant_isolation on campaigns
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table calls enable row level security;
--> statement-breakpoint
alter table calls force row level security;
--> statement-breakpoint
create policy tenant_isolation on calls
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table outcomes enable row level security;
--> statement-breakpoint
alter table outcomes force row level security;
--> statement-breakpoint
create policy tenant_isolation on outcomes
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table follow_ups enable row level security;
--> statement-breakpoint
alter table follow_ups force row level security;
--> statement-breakpoint
create policy tenant_isolation on follow_ups
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table suppression_entries enable row level security;
--> statement-breakpoint
alter table suppression_entries force row level security;
--> statement-breakpoint
create policy tenant_isolation on suppression_entries
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table consents enable row level security;
--> statement-breakpoint
alter table consents force row level security;
--> statement-breakpoint
create policy tenant_isolation on consents
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
alter table audit_logs enable row level security;
--> statement-breakpoint
alter table audit_logs force row level security;
--> statement-breakpoint
create policy tenant_isolation on audit_logs
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
