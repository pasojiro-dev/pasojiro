-- パソ二郎: 「みんなの感想」(コメントと写真)用のテーブル・権限・荒らし対策
-- 使い方: Supabase の SQL Editor に全文を貼り付けて、Run を1回押す。何度流しても壊れない。
--
-- 方針
--  * ログインなしで、誰でも読める・書ける。更新と削除は、下の関数経由でしかできない。
--  * 接続元(IP)はそのままは保存せず、秘密の塩を混ぜた元に戻せない値だけを、回数制限のために使う。
--  * 通報が3件たまった投稿は、読み出しの対象から外れる(画面に出ない)。
--  * 運営による削除は、Supabase の Table Editor で行を消す。

create extension if not exists pgcrypto with schema extensions;

-- ---------- 秘密の塩(anon からは一切見えない) ----------
create table if not exists public.app_secret (
  id int primary key default 1 check (id = 1),
  salt text not null
);
insert into public.app_secret (id, salt)
values (1, encode(extensions.gen_random_bytes(16), 'hex'))
on conflict (id) do nothing;
alter table public.app_secret enable row level security;

-- ---------- 回数制限のための記録 ----------
create table if not exists public.rate_log (
  id bigserial primary key,
  kind text not null,
  ip_hash text not null,
  created_at timestamptz not null default now()
);
create index if not exists rate_log_idx on public.rate_log (kind, ip_hash, created_at desc);
alter table public.rate_log enable row level security;

create or replace function public.client_hash() returns text
language sql stable security definer set search_path = public, extensions as $$
  select encode(
    extensions.digest(
      coalesce(split_part(coalesce(current_setting('request.headers', true)::json ->> 'x-forwarded-for', 'unknown'), ',', 1), 'unknown')
        || ':' || (select salt from public.app_secret where id = 1),
      'sha256'),
    'hex')
$$;

-- p_short_limit: 10分あたりの上限 / p_day_limit: 24時間あたりの上限。超えたら例外 rate_limited
create or replace function public.check_rate(p_kind text, p_short_limit int, p_day_limit int) returns void
language plpgsql security definer set search_path = public, extensions as $$
declare
  h text := public.client_hash();
  n_short int;
  n_day int;
begin
  select count(*) filter (where created_at > now() - interval '10 minutes'), count(*)
    into n_short, n_day
    from public.rate_log
   where kind = p_kind and ip_hash = h and created_at > now() - interval '24 hours';
  if n_short >= p_short_limit or n_day >= p_day_limit then
    raise exception 'rate_limited';
  end if;
  insert into public.rate_log (kind, ip_hash) values (p_kind, h);
  -- 古い記録は、たまに掃除する
  if random() < 0.02 then
    delete from public.rate_log where created_at < now() - interval '3 days';
  end if;
end $$;

-- ---------- 投稿 ----------
create table if not exists public.posts (
  id uuid primary key,
  shop_id text not null check (shop_id ~ '^[a-z0-9-]{1,40}$'),
  nickname text not null default '名無しの二郎' check (char_length(nickname) between 1 and 20),
  body text not null check (char_length(body) between 1 and 300),
  photo_path text check (photo_path is null or photo_path ~ '^[a-z0-9-]{1,40}/[0-9a-f-]{36}\.jpg$'),
  token_hash text not null check (char_length(token_hash) = 64),
  report_count int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists posts_shop_idx on public.posts (shop_id, created_at desc);

alter table public.posts enable row level security;
drop policy if exists posts_read on public.posts;
create policy posts_read on public.posts for select to anon using (report_count < 3);
drop policy if exists posts_insert on public.posts;
create policy posts_insert on public.posts for insert to anon with check (report_count = 0);

-- 列ごとの権限: token_hash と report_count は読ませない・書かせない(token_hash は挿入だけ可)
revoke all on public.posts from anon, authenticated;
grant select (id, shop_id, nickname, body, photo_path, created_at) on public.posts to anon;
grant insert (id, shop_id, nickname, body, photo_path, token_hash) on public.posts to anon;

create or replace function public.posts_before_insert() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  perform public.check_rate('post', 5, 30);
  new.report_count := 0;
  new.created_at := now();
  return new;
end $$;
drop trigger if exists posts_before_insert on public.posts;
create trigger posts_before_insert before insert on public.posts
  for each row execute function public.posts_before_insert();

-- ---------- 通報 ----------
create table if not exists public.reports (
  post_id uuid not null references public.posts (id) on delete cascade,
  ip_hash text not null,
  created_at timestamptz not null default now(),
  primary key (post_id, ip_hash)
);
alter table public.reports enable row level security;

create or replace function public.report_post(p_id uuid) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  perform public.check_rate('report', 10, 60);
  insert into public.reports (post_id, ip_hash) values (p_id, public.client_hash())
    on conflict do nothing;
  if found then
    update public.posts set report_count = report_count + 1 where id = p_id;
  end if;
end $$;
revoke all on function public.report_post(uuid) from public;
grant execute on function public.report_post(uuid) to anon;

-- ---------- 自分の投稿の削除(投稿時に端末へ保存した合言葉が必要) ----------
create or replace function public.delete_my_post(p_id uuid, p_token text) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
declare n int;
begin
  perform public.check_rate('delete', 10, 60);
  delete from public.posts
   where id = p_id and token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex');
  get diagnostics n = row_count;
  return n > 0;
end $$;
revoke all on function public.delete_my_post(uuid, text) from public;
grant execute on function public.delete_my_post(uuid, text) to anon;

-- ---------- 写真の置き場(公開・JPEGのみ・1MBまで) ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('photos', 'photos', true, 1048576, array['image/jpeg'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

create or replace function public.upload_allowed() returns boolean
language plpgsql security definer set search_path = public, extensions as $$
begin
  perform public.check_rate('upload', 5, 30);
  return true;
end $$;
revoke all on function public.upload_allowed() from public;
grant execute on function public.upload_allowed() to anon;

drop policy if exists paso_photos_insert on storage.objects;
create policy paso_photos_insert on storage.objects for insert to anon
  with check (
    bucket_id = 'photos'
    and name ~ '^[a-z0-9-]{1,40}/[0-9a-f-]{36}\.jpg$'
    and public.upload_allowed()
  );
