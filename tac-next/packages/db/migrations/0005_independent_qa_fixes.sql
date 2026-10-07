-- 0005: 独立 QA（2026-10-06）の指摘への対応
-- IQA-05: 通話 ID だけで特定するのは、プロバイダの ID がまだ記録されていない通話に限る。
--         記録済みの ID と違う ID を名乗る Webhook では、その通話の状態を動かさない。
create or replace function locate_provider_call(p_provider text, p_provider_call_id text, p_call_id uuid)
  returns table (organization_id uuid, call_id uuid)
  language sql stable security definer set search_path = public, pg_temp
  as $$
    (select c.organization_id, c.id from calls c
      where p_provider_call_id is not null
        and c.provider = p_provider and c.provider_call_id = p_provider_call_id)
    union all
    (select c.organization_id, c.id from calls c
      where p_call_id is not null and c.id = p_call_id
        and (c.provider is null or c.provider = p_provider)
        and (c.provider_call_id is null or p_provider_call_id is null
             or c.provider_call_id = p_provider_call_id))
    limit 1
  $$;
--> statement-breakpoint
-- IQA-10: パスワードを照合する前に、試行の枠を 1 回分だけ不可分に予約する。
-- 行ロック（FOR UPDATE）で直列化するので、同時に送られた試行も上限を超えて照合されない。
-- ロック中なら解除時刻を返し（照合しない）、予約できたら null を返す。上限ちょうどの試行で次からロックする。
create function auth_throttle_reserve(
  p_key text, p_max integer, p_window_ms integer, p_lock_ms integer, p_now timestamptz
) returns timestamptz
  language plpgsql security definer set search_path = public, pg_temp
  as $$
  declare
    r auth_throttle%rowtype;
    v_failures integer;
    v_window timestamptz;
  begin
    if p_max < 1 or p_window_ms < 1 or p_lock_ms < 1 then
      raise exception 'invalid throttle policy' using errcode = '22023';
    end if;
    insert into auth_throttle (key, failures, window_started_at) values (p_key, 0, p_now)
      on conflict (key) do nothing;
    select * into r from auth_throttle where key = p_key for update;
    if r.locked_until is not null and r.locked_until > p_now then
      return r.locked_until;
    end if;
    if p_now - r.window_started_at >= p_window_ms * interval '1 millisecond' then
      v_failures := 1; v_window := p_now;
    else
      v_failures := r.failures + 1; v_window := r.window_started_at;
    end if;
    if v_failures >= p_max then
      update auth_throttle set failures = 0, window_started_at = p_now,
        locked_until = p_now + p_lock_ms * interval '1 millisecond' where key = p_key;
    else
      update auth_throttle set failures = v_failures, window_started_at = v_window
        where key = p_key;
    end if;
    return null;
  end
  $$;
--> statement-breakpoint
-- 照合に成功した試行の予約を 1 回分戻す（IP 単位など、成功してもリセットしないキー用）
create function auth_throttle_refund(p_key text) returns void
  language sql security definer set search_path = public, pg_temp
  as $$ update auth_throttle set failures = greatest(failures - 1, 0) where key = p_key $$;
--> statement-breakpoint
alter function auth_throttle_reserve(text, integer, integer, integer, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function auth_throttle_refund(text) owner to tac_definer;
--> statement-breakpoint
revoke execute on function
  auth_throttle_reserve(text, integer, integer, integer, timestamptz),
  auth_throttle_refund(text)
  from public;
--> statement-breakpoint
grant execute on function
  auth_throttle_reserve(text, integer, integer, integer, timestamptz),
  auth_throttle_refund(text)
  to tac_app;
