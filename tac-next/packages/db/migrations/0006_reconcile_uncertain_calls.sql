-- 確定しない発信の照合（ADR-0016）。REQUESTED のまま・プロバイダの ID が無い通話を、全組織から古い順に返す。
-- 照合の処理は組織を決める前に対象を探す必要があるので、テナントをまたぐ照会を SECURITY DEFINER 関数に閉じ込める
-- （返すのは組織 ID と通話 ID だけ。中身は組織を決めてから RLS の下で読む）。
create index calls_uncertain_idx on calls (created_at, id)
  where status = 'REQUESTED' and provider_call_id is null;
--> statement-breakpoint
create function list_uncertain_calls(p_older_than timestamptz, p_limit integer)
  returns table (organization_id uuid, call_id uuid)
  language sql stable security definer set search_path = public, pg_temp
  as $$
    select c.organization_id, c.id from calls c
      where c.status = 'REQUESTED' and c.provider_call_id is null and c.created_at < p_older_than
      order by c.created_at, c.id
      limit greatest(least(p_limit, 500), 0)
  $$;
--> statement-breakpoint
alter function list_uncertain_calls(timestamptz, integer) owner to tac_definer;
--> statement-breakpoint
revoke execute on function list_uncertain_calls(timestamptz, integer) from public;
--> statement-breakpoint
grant execute on function list_uncertain_calls(timestamptz, integer) to tac_app;
