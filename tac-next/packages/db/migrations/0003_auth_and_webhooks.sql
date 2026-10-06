-- 0003: 認証（ユーザー・所属・セッション）と Webhook の受信（Phase 3・7）
-- ログイン・セッションの解決・Webhook からの通話の特定は、組織が決まる前に行う（テナントをまたぐ）。
-- アプリのロール（tac_app）にはこれらの表を直接読ませず、目的ごとの SECURITY DEFINER 関数だけを実行させる。
-- 関数の所有者 tac_definer は NOLOGIN・BYPASSRLS（RLS を通らずに必要な行だけを読む）。

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'tac_definer') then
    create role tac_definer nologin bypassrls;
  end if;
end
$$;
--> statement-breakpoint
create table users (
  id uuid primary key,
  email text not null check (email = lower(btrim(email)) and length(email) between 3 and 320),
  display_name text not null check (length(btrim(display_name)) > 0),
  password_hash text not null,
  created_at timestamptz not null default now(),
  disabled_at timestamptz
);
--> statement-breakpoint
create unique index users_email_uq on users (email);
--> statement-breakpoint
create table memberships (
  organization_id uuid not null references organizations (id),
  user_id uuid not null references users (id),
  role text not null check (role in ('OWNER', 'ADMIN', 'MANAGER', 'OPERATOR', 'VIEWER')),
  created_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);
--> statement-breakpoint
-- セッションの ID と CSRF トークンは HMAC のハッシュだけを保存する。所属を外したらセッションも消える
create table sessions (
  id_hash text primary key,
  organization_id uuid not null,
  user_id uuid not null,
  csrf_hash text not null,
  created_at timestamptz not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  foreign key (organization_id, user_id) references memberships (organization_id, user_id)
    on delete cascade
);
--> statement-breakpoint
create index sessions_user_idx on sessions (user_id);
--> statement-breakpoint
-- 受け取った Webhook の生データと処理状況。(provider, event_id) で重複排除
create table webhook_events (
  provider text not null check (length(provider) between 1 and 64),
  event_id text not null check (length(event_id) between 1 and 255),
  payload jsonb not null,
  received_at timestamptz not null,
  claimed_at timestamptz,
  processed_at timestamptz,
  primary key (provider, event_id)
);
--> statement-breakpoint
create table call_events (
  id bigint generated always as identity primary key,
  organization_id uuid not null references organizations (id),
  call_id uuid not null,
  provider text not null,
  event_id text not null,
  status text not null check (status in (
    'REQUESTED', 'DIALING', 'RINGING', 'IN_PROGRESS',
    'ENDED', 'FAILED', 'NO_ANSWER', 'BUSY', 'CANCELED')),
  applied boolean not null,
  occurred_at timestamptz not null,
  received_at timestamptz not null,
  constraint call_events_provider_event_uq unique (provider, event_id),
  foreign key (organization_id, call_id) references calls (organization_id, id)
);
--> statement-breakpoint
create index call_events_call_idx on call_events (organization_id, call_id, id);
--> statement-breakpoint
alter table call_events enable row level security;
--> statement-breakpoint
alter table call_events force row level security;
--> statement-breakpoint
create policy tenant_isolation on call_events
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
grant select, insert on call_events to tac_app;
--> statement-breakpoint
alter table memberships enable row level security;
--> statement-breakpoint
alter table memberships force row level security;
--> statement-breakpoint
create policy tenant_isolation on memberships
  using (organization_id = app_current_org()) with check (organization_id = app_current_org());
--> statement-breakpoint
-- users・memberships・sessions・webhook_events は tac_app に権限を与えない（関数だけを通す）
grant usage on schema public to tac_definer;
--> statement-breakpoint
grant select on users, memberships, calls to tac_definer;
--> statement-breakpoint
grant select, insert, update on sessions, webhook_events to tac_definer;
--> statement-breakpoint
create function auth_find_for_login(p_email text)
  returns table (user_id uuid, display_name text, password_hash text, organization_id uuid, role text)
  language sql stable security definer set search_path = public, pg_temp
  as $$
    select u.id, u.display_name, u.password_hash, m.organization_id, m.role
    from users u left join memberships m on m.user_id = u.id
    where u.email = p_email and u.disabled_at is null
    order by m.organization_id
  $$;
--> statement-breakpoint
create function auth_create_session(
  p_id_hash text, p_user_id uuid, p_organization_id uuid, p_csrf_hash text,
  p_created_at timestamptz, p_expires_at timestamptz
) returns void
  language plpgsql security definer set search_path = public, pg_temp
  as $$
  begin
    if not exists (
      select 1 from memberships m join users u on u.id = m.user_id
      where m.user_id = p_user_id and m.organization_id = p_organization_id and u.disabled_at is null
    ) then
      raise exception 'an active membership is required' using errcode = '42501';
    end if;
    insert into sessions (id_hash, organization_id, user_id, csrf_hash, created_at, expires_at)
    values (p_id_hash, p_organization_id, p_user_id, p_csrf_hash, p_created_at, p_expires_at);
  end
  $$;
--> statement-breakpoint
create function auth_resolve_session(p_id_hash text, p_now timestamptz)
  returns table (user_id uuid, organization_id uuid, role text, display_name text, csrf_hash text, expires_at timestamptz)
  language sql stable security definer set search_path = public, pg_temp
  as $$
    select s.user_id, s.organization_id, m.role, u.display_name, s.csrf_hash, s.expires_at
    from sessions s
    join memberships m on m.organization_id = s.organization_id and m.user_id = s.user_id
    join users u on u.id = s.user_id
    where s.id_hash = p_id_hash and s.revoked_at is null and s.expires_at > p_now
      and u.disabled_at is null
  $$;
--> statement-breakpoint
create function auth_revoke_session(p_id_hash text, p_at timestamptz) returns void
  language sql security definer set search_path = public, pg_temp
  as $$
    update sessions set revoked_at = p_at where id_hash = p_id_hash and revoked_at is null
  $$;
--> statement-breakpoint
-- 処理権を取る：初めて・前回の処理が失敗して手放された・処理中のまま 60 秒以上経った、のどれかなら true
create function webhook_begin(
  p_provider text, p_event_id text, p_payload jsonb, p_received_at timestamptz
) returns boolean
  language plpgsql security definer set search_path = public, pg_temp
  as $$
  declare
    claimed boolean;
  begin
    insert into webhook_events (provider, event_id, payload, received_at, claimed_at)
    values (p_provider, p_event_id, p_payload, p_received_at, p_received_at)
    on conflict (provider, event_id) do update set claimed_at = excluded.claimed_at
      where webhook_events.processed_at is null
        and (webhook_events.claimed_at is null
             or webhook_events.claimed_at < excluded.claimed_at - interval '60 seconds')
    returning true into claimed;
    return coalesce(claimed, false);
  end
  $$;
--> statement-breakpoint
create function webhook_complete(p_provider text, p_event_id text, p_at timestamptz) returns void
  language sql security definer set search_path = public, pg_temp
  as $$
    update webhook_events set processed_at = p_at, claimed_at = null
    where provider = p_provider and event_id = p_event_id
  $$;
--> statement-breakpoint
create function webhook_release(p_provider text, p_event_id text) returns void
  language sql security definer set search_path = public, pg_temp
  as $$
    update webhook_events set claimed_at = null
    where provider = p_provider and event_id = p_event_id and processed_at is null
  $$;
--> statement-breakpoint
-- Webhook の通話を特定する（プロバイダの ID → 無ければ発信時に渡した通話 ID。プロバイダが違うものは返さない）
create function locate_provider_call(p_provider text, p_provider_call_id text, p_call_id uuid)
  returns table (organization_id uuid, call_id uuid)
  language sql stable security definer set search_path = public, pg_temp
  as $$
    (select c.organization_id, c.id from calls c
      where p_provider_call_id is not null
        and c.provider = p_provider and c.provider_call_id = p_provider_call_id)
    union all
    (select c.organization_id, c.id from calls c
      where p_call_id is not null and c.id = p_call_id
        and (c.provider is null or c.provider = p_provider))
    limit 1
  $$;
--> statement-breakpoint
alter function auth_find_for_login(text) owner to tac_definer;
--> statement-breakpoint
alter function auth_create_session(text, uuid, uuid, text, timestamptz, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function auth_resolve_session(text, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function auth_revoke_session(text, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function webhook_begin(text, text, jsonb, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function webhook_complete(text, text, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function webhook_release(text, text) owner to tac_definer;
--> statement-breakpoint
alter function locate_provider_call(text, text, uuid) owner to tac_definer;
--> statement-breakpoint
revoke execute on function
  auth_find_for_login(text),
  auth_create_session(text, uuid, uuid, text, timestamptz, timestamptz),
  auth_resolve_session(text, timestamptz),
  auth_revoke_session(text, timestamptz),
  webhook_begin(text, text, jsonb, timestamptz),
  webhook_complete(text, text, timestamptz),
  webhook_release(text, text),
  locate_provider_call(text, text, uuid)
  from public;
--> statement-breakpoint
grant execute on function
  auth_find_for_login(text),
  auth_create_session(text, uuid, uuid, text, timestamptz, timestamptz),
  auth_resolve_session(text, timestamptz),
  auth_revoke_session(text, timestamptz),
  webhook_begin(text, text, jsonb, timestamptz),
  webhook_complete(text, text, timestamptz),
  webhook_release(text, text),
  locate_provider_call(text, text, uuid)
  to tac_app;
