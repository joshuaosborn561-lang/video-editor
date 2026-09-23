-- Desk metadata and private footage. Apply on a dedicated Supabase project.
-- The API uses the service role from the server. No anon or authenticated policies.

create table if not exists public.desk_projects (
  id text primary key,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.desk_projects enable row level security;

insert into storage.buckets (id, name, public, file_size_limit)
values ('desk', 'desk', false, 5368709120)
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;
