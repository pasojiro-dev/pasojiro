-- パソ二郎: 「いまの行列」(待ち時間の報告)を足す(あとから足す用)
-- 使い方: Supabase の SQL Editor に全文を貼り付けて、Run を1回押す。何度流しても壊れない。
-- 先に supabase/schema.sql を流してあること。このファイルの内容は schema.sql の末尾にも入っている。
--
-- 方針
--  * 報告は「行列の人数を5段階」だけ。位置情報は受け取らない(店の近くにいるかは確認しない)。
--  * anon は wait_reports 表に直接さわれない。関数 report_wait(報告)と get_wait_all(集計の読み出し)だけが使える。
--  * 同じ接続元から同じ店へは10分に1回まで。全体の回数も check_rate で制限する。
--  * 読み出しは、直近60分の中央値と件数だけ。個々の報告は返さない。24時間より古い報告は、たまに掃除する。

create table if not exists public.wait_reports (
  id bigserial primary key,
  shop_id text not null check (shop_id ~ '^[a-z0-9-]{1,40}$'),
  level smallint not null check (level between 0 and 4),  -- 0:並びなし 1:1〜5人 2:6〜10人 3:11〜20人 4:21人以上
  ip_hash text not null,
  created_at timestamptz not null default now()
);
create index if not exists wait_reports_idx on public.wait_reports (shop_id, created_at desc);
alter table public.wait_reports enable row level security;   -- ポリシーを作らない = anon は直接読み書きできない
revoke all on public.wait_reports from anon, authenticated;

create or replace function public.report_wait(p_shop text, p_level int) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if p_level is null or p_level < 0 or p_level > 4 or p_shop is null or p_shop !~ '^[a-z0-9-]{1,40}$' then
    raise exception 'bad_request';
  end if;
  if exists (select 1 from public.wait_reports
              where shop_id = p_shop and ip_hash = public.client_hash()
                and created_at > now() - interval '10 minutes') then
    raise exception 'rate_limited';
  end if;
  perform public.check_rate('wait', 6, 40);
  insert into public.wait_reports (shop_id, level, ip_hash)
    values (p_shop, p_level, public.client_hash());
  if random() < 0.02 then
    delete from public.wait_reports where created_at < now() - interval '24 hours';
  end if;
end $$;
revoke all on function public.report_wait(text, int) from public;
grant execute on function public.report_wait(text, int) to anon;

create or replace function public.get_wait_all() returns table(shop_id text, level int, n int, newest timestamptz)
language sql stable security definer set search_path = public as $$
  select w.shop_id,
         (percentile_disc(0.5) within group (order by w.level))::int,
         count(*)::int,
         max(w.created_at)
    from public.wait_reports w
   where w.created_at > now() - interval '60 minutes'
   group by w.shop_id
$$;
revoke all on function public.get_wait_all() from public;
grant execute on function public.get_wait_all() to anon;
