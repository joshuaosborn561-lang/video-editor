-- YouTube desk, on the existing database. Do not create a new Supabase project.
-- Schema youtube is separate from public lead tables. The login is youtube_desk.
-- Its password is not stored here. Put the session-pooler URL in SUPABASE_DB_URL.
-- Do not put the service role or the anon key on the Railway service.

create schema if not exists youtube;

create table if not exists youtube.projects (
  id text primary key,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists youtube.files (
  project_id text not null references youtube.projects(id) on delete cascade,
  name text not null,
  oid oid not null,
  primary key (project_id, name)
);

alter table youtube.projects enable row level security;
alter table youtube.files enable row level security;

revoke all on schema youtube from public, anon, authenticated, service_role;
revoke all on all tables in schema youtube from public, anon, authenticated, service_role;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'youtube_desk') then
    raise exception 'create login youtube_desk before applying grants';
  end if;
end $$;

alter role youtube_desk set search_path = youtube;

grant connect on database postgres to youtube_desk;
grant usage on schema youtube to youtube_desk;
grant select, insert, update, delete on youtube.projects to youtube_desk;
grant select, insert, update, delete on youtube.files to youtube_desk;

drop policy if exists youtube_desk_all on youtube.projects;
create policy youtube_desk_all on youtube.projects
  for all to youtube_desk using (true) with check (true);

drop policy if exists youtube_desk_all on youtube.files;
create policy youtube_desk_all on youtube.files
  for all to youtube_desk using (true) with check (true);

revoke all on all tables in schema public from youtube_desk;
revoke all on all sequences in schema public from youtube_desk;
revoke all on all functions in schema public from youtube_desk;

-- Lead export functions were executable by every role. Re-grant them to the
-- roles that already existed, and leave youtube_desk out.
do $$ declare
  fn regprocedure;
  kind "char";
  r text;
  spec text;
begin
  for fn, kind in
    select p.oid::regprocedure, p.prokind
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proowner = 'postgres'::regrole
  loop
    spec := case when kind = 'p' then 'procedure' else 'function' end;
    execute format('revoke execute on %s %s from public', spec, fn);
    execute format('revoke execute on %s %s from youtube_desk', spec, fn);
    for r in
      select rolname from pg_roles
      where rolsuper is false and rolname <> 'youtube_desk' and rolname not like 'pg_%'
    loop
      execute format('grant execute on %s %s to %I', spec, fn, r);
    end loop;
  end loop;
end $$;

alter default privileges in schema public revoke execute on functions from public;
alter default privileges in schema public
  grant execute on functions to anon, authenticated, authenticator, service_role, leadtopup_app, dashboard_user, cli_login_postgres;

comment on schema youtube is 'YouTube desk. Login youtube_desk cannot read lead tables or call lead export functions.';
