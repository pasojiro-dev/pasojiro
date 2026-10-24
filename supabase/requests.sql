-- パソ二郎: 「店の追加をリクエスト」を受け付ける(あとから足す用)
-- 使い方: Supabase の SQL Editor に全文を貼り付けて、Run を1回押す。何度流しても壊れない。
-- 先に supabase/schema.sql を流してあること。このファイルの内容は schema.sql の末尾にも入っている。
--
-- 方針
--  * ログインなしで、誰でも送れる。読めるのは運営だけ(Supabase の Table Editor の shop_requests で見る)。
--  * 連絡先は集めない。接続元(IP)は元に戻せない値だけを回数制限に使う(10分に3件、1日に10件まで)。
--  * 運営は、読んだら status を 'added'(追加した)・'rejected'(見送り)・'dup'(重複)などに書き換えて整理する。

create table if not exists public.shop_requests (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 1 and 60),
  place text not null check (char_length(place) between 1 and 120),
  url text check (url is null or (char_length(url) <= 300 and url ~* '^https?://')),
  note text check (note is null or char_length(note) <= 300),
  status text not null default 'new',
  created_at timestamptz not null default now()
);
alter table public.shop_requests enable row level security;

drop policy if exists shop_requests_insert on public.shop_requests;
create policy shop_requests_insert on public.shop_requests for insert to anon with check (true);

revoke all on public.shop_requests from anon, authenticated;
grant insert (name, place, url, note) on public.shop_requests to anon;

create or replace function public.shop_requests_before_insert() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  perform public.check_rate('request', 3, 10);
  new.status := 'new';
  new.created_at := now();
  return new;
end $$;
drop trigger if exists shop_requests_before_insert on public.shop_requests;
create trigger shop_requests_before_insert before insert on public.shop_requests
  for each row execute function public.shop_requests_before_insert();
