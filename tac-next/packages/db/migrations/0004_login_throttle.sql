-- 0004: ログイン試行の記録（総当たり対策、ADR-0013 の続き）
-- キーはアプリ側で HMAC したもの（メールアドレス・IP をそのまま保存しない）。
-- テナントが決まる前に使うので、tac_app には表の権限を与えず、tac_definer 所有の関数だけを実行させる。

create table auth_throttle (
  key text primary key check (length(key) between 1 and 200),
  failures integer not null check (failures >= 0),
  window_started_at timestamptz not null,
  locked_until timestamptz
);
--> statement-breakpoint
grant select, insert, update, delete on auth_throttle to tac_definer;
--> statement-breakpoint
create function auth_throttle_locked_until(p_keys text[], p_now timestamptz) returns timestamptz
  language sql stable security definer set search_path = public, pg_temp
  as $$
    select max(locked_until) from auth_throttle where key = any(p_keys) and locked_until > p_now
  $$;
--> statement-breakpoint
-- 失敗を 1 回数える。窓（p_window_ms）を過ぎていたら数え直す。上限に達したらロックして数え直す。
-- UPDATE の行ロックで直列化されるので、同時の失敗も取りこぼさない
create function auth_throttle_fail(
  p_key text, p_max integer, p_window_ms integer, p_lock_ms integer, p_now timestamptz
) returns void
  language plpgsql security definer set search_path = public, pg_temp
  as $$
  begin
    if p_max < 1 or p_window_ms < 1 or p_lock_ms < 1 then
      raise exception 'invalid throttle policy' using errcode = '22023';
    end if;
    insert into auth_throttle (key, failures, window_started_at) values (p_key, 0, p_now)
      on conflict (key) do nothing;
    update auth_throttle set
      failures = case when p_now - window_started_at >= p_window_ms * interval '1 millisecond'
                      then 1 else failures + 1 end,
      window_started_at = case when p_now - window_started_at >= p_window_ms * interval '1 millisecond'
                               then p_now else window_started_at end
      where key = p_key;
    update auth_throttle set
      failures = 0, window_started_at = p_now, locked_until = p_now + p_lock_ms * interval '1 millisecond'
      where key = p_key and failures >= p_max;
  end
  $$;
--> statement-breakpoint
create function auth_throttle_reset(p_key text) returns void
  language sql security definer set search_path = public, pg_temp
  as $$ delete from auth_throttle where key = p_key $$;
--> statement-breakpoint
alter function auth_throttle_locked_until(text[], timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function auth_throttle_fail(text, integer, integer, integer, timestamptz) owner to tac_definer;
--> statement-breakpoint
alter function auth_throttle_reset(text) owner to tac_definer;
--> statement-breakpoint
revoke execute on function
  auth_throttle_locked_until(text[], timestamptz),
  auth_throttle_fail(text, integer, integer, integer, timestamptz),
  auth_throttle_reset(text)
  from public;
--> statement-breakpoint
grant execute on function
  auth_throttle_locked_until(text[], timestamptz),
  auth_throttle_fail(text, integer, integer, integer, timestamptz),
  auth_throttle_reset(text)
  to tac_app;
