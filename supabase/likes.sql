-- パソ二郎: 投稿写真への「いいね!」を足す(あとから足す用)
-- 使い方: Supabase の SQL Editor に全文を貼り付けて、Run を1回押す。何度流しても壊れない。
-- 先に supabase/schema.sql を流してあること。このファイルの内容は schema.sql の末尾にも入っている。
--
-- 方針
--  * 1つの投稿に、1つの端末は1回だけ「いいね!」できる(端末ごとの合言葉を、塩つきで元に戻せない値にして記録)。
--  * 取り消しもできる。短い時間に押しすぎると、回数制限で止まる。
--  * anon は likes 表に直接さわれない。関数 like_post / unlike_post だけが使える。

alter table public.posts add column if not exists like_count int not null default 0;
grant select (like_count) on public.posts to anon;

create table if not exists public.likes (
  post_id uuid not null references public.posts (id) on delete cascade,
  dev_hash text not null,
  created_at timestamptz not null default now(),
  primary key (post_id, dev_hash)
);
alter table public.likes enable row level security;

create or replace function public.like_post(p_id uuid, p_dev text) returns int
language plpgsql security definer set search_path = public, extensions as $$
declare h text; n int;
begin
  if p_dev is null or char_length(p_dev) < 16 or char_length(p_dev) > 64 then
    raise exception 'bad_device';
  end if;
  perform public.check_rate('like', 20, 200);
  h := encode(extensions.digest(p_dev || ':' || (select salt from public.app_secret where id = 1), 'sha256'), 'hex');
  insert into public.likes (post_id, dev_hash) values (p_id, h) on conflict do nothing;
  if found then
    update public.posts set like_count = like_count + 1 where id = p_id;
  end if;
  select like_count into n from public.posts where id = p_id;
  return coalesce(n, 0);
end $$;
revoke all on function public.like_post(uuid, text) from public;
grant execute on function public.like_post(uuid, text) to anon;

create or replace function public.unlike_post(p_id uuid, p_dev text) returns int
language plpgsql security definer set search_path = public, extensions as $$
declare h text; n int;
begin
  if p_dev is null or char_length(p_dev) < 16 or char_length(p_dev) > 64 then
    raise exception 'bad_device';
  end if;
  perform public.check_rate('like', 20, 200);
  h := encode(extensions.digest(p_dev || ':' || (select salt from public.app_secret where id = 1), 'sha256'), 'hex');
  delete from public.likes where post_id = p_id and dev_hash = h;
  if found then
    update public.posts set like_count = greatest(like_count - 1, 0) where id = p_id;
  end if;
  select like_count into n from public.posts where id = p_id;
  return coalesce(n, 0);
end $$;
revoke all on function public.unlike_post(uuid, text) from public;
grant execute on function public.unlike_post(uuid, text) to anon;
