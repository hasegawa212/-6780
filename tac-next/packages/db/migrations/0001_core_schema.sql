-- 0001: 最初の縦切りに必要なテーブルと制約（docs/DATABASE.md）
-- 文は区切り行（statement-breakpoint）で分け、1文ずつ実行する（ドライバに依存しない）
-- 全テーブルに organization_id（organizations は id 自身、system_controls は全体の設定で例外）。
-- 子テーブルは (organization_id, 親 id) の複合外部キーで、別テナントの行を参照できないようにする。

create table organizations (
  id uuid primary key,
  company_name text not null check (length(btrim(company_name)) > 0),
  ai_voice_outbound_enabled boolean not null default false,
  paused boolean not null default false,
  max_concurrent_calls integer not null check (max_concurrent_calls > 0),
  created_at timestamptz not null default now()
);
--> statement-breakpoint
create table contacts (
  id uuid primary key,
  organization_id uuid not null references organizations (id),
  display_name text not null,
  phone_e164 text not null check (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  time_zone text,
  created_at timestamptz not null default now(),
  unique (organization_id, id)
);
--> statement-breakpoint
create index contacts_org_phone_idx on contacts (organization_id, phone_e164);
--> statement-breakpoint
create table campaigns (
  id uuid primary key,
  organization_id uuid not null references organizations (id),
  product text not null,
  caller_id_e164 text not null check (caller_id_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  calling_window jsonb not null,
  allowed_country_codes text[] not null,
  daily_cap integer check (daily_cap >= 0),
  per_number_daily_limit integer not null check (per_number_daily_limit >= 0),
  max_attempts integer not null check (max_attempts >= 0),
  paused boolean not null default false,
  created_at timestamptz not null default now(),
  unique (organization_id, id)
);
--> statement-breakpoint
create table calls (
  id uuid primary key,
  organization_id uuid not null references organizations (id),
  contact_id uuid not null,
  campaign_id uuid not null,
  to_e164 text not null check (to_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  from_e164 text not null check (from_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  mode text not null check (mode in ('HUMAN_DIALED', 'AI_VOICE')),
  status text not null check (status in (
    'REQUESTED', 'DIALING', 'RINGING', 'IN_PROGRESS',
    'ENDED', 'FAILED', 'NO_ANSWER', 'BUSY', 'CANCELED')),
  idempotency_key text not null check (length(idempotency_key) between 1 and 255),
  request_fingerprint text not null,
  requested_by text not null,
  agent_name text not null,
  provider text,
  provider_call_id text,
  created_at timestamptz not null,
  updated_at timestamptz not null default now(),
  unique (organization_id, id),
  constraint calls_org_idempotency_key_uq unique (organization_id, idempotency_key),
  constraint calls_provider_call_id_uq unique (provider, provider_call_id),
  foreign key (organization_id, contact_id) references contacts (organization_id, id),
  foreign key (organization_id, campaign_id) references campaigns (organization_id, id)
);
--> statement-breakpoint
-- 回線上の通話は番号ごとに1件（QA-NX-01: 別々の冪等キーでの同時発信を DB で止める）
create unique index calls_one_active_per_number_uq on calls (organization_id, to_e164)
  where status in ('REQUESTED', 'DIALING', 'RINGING', 'IN_PROGRESS');
--> statement-breakpoint
create index calls_org_created_at_idx on calls (organization_id, created_at desc);
--> statement-breakpoint
create index calls_org_contact_idx on calls (organization_id, contact_id);
--> statement-breakpoint
-- 結果は通話ごとに1件（call_id が主キー）
create table outcomes (
  call_id uuid primary key,
  organization_id uuid not null references organizations (id),
  code text not null check (length(code) > 0),
  recorded_by text not null,
  recorded_at timestamptz not null,
  foreign key (organization_id, call_id) references calls (organization_id, id)
);
--> statement-breakpoint
create table follow_ups (
  id uuid primary key,
  organization_id uuid not null references organizations (id),
  contact_id uuid not null,
  kind text not null check (kind in ('FOLLOW_UP', 'CALLBACK', 'RETRY', 'APPOINTMENT')),
  due_at timestamptz not null,
  status text not null check (status in ('OPEN', 'DONE', 'CANCELED')),
  source_call_id uuid not null,
  created_at timestamptz not null default now(),
  foreign key (organization_id, contact_id) references contacts (organization_id, id),
  foreign key (organization_id, source_call_id) references calls (organization_id, id)
);
--> statement-breakpoint
create index follow_ups_open_due_idx on follow_ups (organization_id, due_at) where status = 'OPEN';
--> statement-breakpoint
-- 抑止（DNC）。解除は lifted_at を入れる（行は消さない）。有効な抑止は番号ごとに1件
create table suppression_entries (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations (id),
  phone_e164 text not null check (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  reason text not null,
  source text not null,
  actor_id text not null,
  created_at timestamptz not null default now(),
  lifted_at timestamptz,
  lifted_by text,
  lift_reason text
);
--> statement-breakpoint
create unique index suppression_active_uq on suppression_entries (organization_id, phone_e164)
  where lifted_at is null;
--> statement-breakpoint
create table consents (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations (id),
  contact_id uuid not null,
  scope text not null check (scope in ('AI_VOICE_OUTBOUND')),
  legal_basis text not null,
  source text not null,
  granted_at timestamptz not null,
  revoked_at timestamptz,
  foreign key (organization_id, contact_id) references contacts (organization_id, id)
);
--> statement-breakpoint
create index consents_contact_idx on consents (organization_id, contact_id, scope);
--> statement-breakpoint
create table audit_logs (
  id bigint generated always as identity primary key,
  organization_id uuid not null references organizations (id),
  actor_id text not null,
  action text not null,
  resource text not null,
  before jsonb,
  after jsonb,
  at timestamptz not null
);
--> statement-breakpoint
create index audit_logs_org_at_idx on audit_logs (organization_id, at desc);
--> statement-breakpoint
-- システム全体の緊急停止（STOP ALL OUTBOUND CALLS、ADR-0006）。テナントをまたぐ唯一の行
create table system_controls (
  id boolean primary key default true check (id),
  outbound_stopped boolean not null default false,
  updated_at timestamptz not null default now(),
  updated_by text
);
--> statement-breakpoint
insert into system_controls (id, outbound_stopped) values (true, false);
