-- saesr contract v2 schema. Paste the whole file into the Supabase SQL editor and run it.
-- Idempotent: safe to re-run. The backend writes with the secret key (bypasses RLS);
-- the dashboard reads with the publishable key (select only) and subscribes via Realtime.

-- 1. Drop the v1 tables (test data only). The v1 incidents table is detected by its "camera"
--    column, so re-running this script never drops the v2 incidents table.
drop table if exists public.agent_actions;
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'incidents' and column_name = 'camera'
  ) then
    drop table public.incidents cascade;
  end if;
end $$;

-- 2. Tables
create table if not exists public.events (
  event_id    text primary key,
  site_id     text not null default 'site-demo',
  camera_id   text not null,
  type        text not null,
  confidence  double precision,
  ts          timestamptz not null default now(),
  zone_id     text,
  centroid_x  double precision,
  centroid_y  double precision,
  metrics     jsonb not null default '{}'::jsonb,
  keyframes   text[] not null default '{}',
  clip_url    text
);

create table if not exists public.incidents (
  id              text primary key,  -- incident_id
  site_id         text not null default 'site-demo',
  camera_id       text not null,
  type            text not null,
  zone_id         text,
  status          text not null default 'verifying',
  severity        int check (severity between 1 and 5),
  opened_at       timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  closed_at       timestamptz,
  triage          jsonb,
  coach           jsonb,
  relay           jsonb not null default '{}'::jsonb,
  low_confidence  boolean not null default false,
  keyframes       text[] not null default '{}',
  clip_url        text
);

create table if not exists public.relay_trace (
  id              bigint generated always as identity primary key,
  incident_id     text not null references public.incidents(id) on delete cascade,
  agent           text not null,
  model           text,
  started_at      timestamptz not null,
  ended_at        timestamptz,
  output_summary  text,
  error           text,
  unique (incident_id, agent, started_at)
);

create table if not exists public.actions (
  action_id    text primary key,
  incident_id  text not null references public.incidents(id) on delete cascade,
  kind         text not null,
  status       text not null default 'pending',
  params       jsonb not null default '{}'::jsonb,
  approved_by  text,
  executed_at  timestamptz,
  updated_at   timestamptz not null default now()
);

create table if not exists public.corrective_records (
  id            text primary key,
  incident_id   text not null references public.incidents(id) on delete cascade,
  description   text,
  standard_tag  text,
  created_at    timestamptz not null default now()
);

create table if not exists public.batch_holds (
  id            text primary key,
  incident_id   text not null references public.incidents(id) on delete cascade,
  camera_id     text,
  window_start  timestamptz,
  window_end    timestamptz,
  status        text not null default 'held'
);

-- 3. Indexes for the dashboard's reads
create index if not exists incidents_camera_id_idx     on public.incidents (camera_id);
create index if not exists incidents_type_idx          on public.incidents (type);
create index if not exists incidents_opened_at_idx     on public.incidents (opened_at desc);
create index if not exists events_ts_idx               on public.events (ts desc);
create index if not exists events_camera_id_idx        on public.events (camera_id);
create index if not exists relay_trace_incident_id_idx on public.relay_trace (incident_id);
create index if not exists actions_incident_id_idx     on public.actions (incident_id);
create index if not exists corrective_records_incident_id_idx on public.corrective_records (incident_id);
create index if not exists batch_holds_incident_id_idx on public.batch_holds (incident_id);

-- 4. RLS: read-only for anon and authenticated; writes only with the secret key (service role)
do $$
declare
  t text;
begin
  foreach t in array array['events', 'incidents', 'relay_trace', 'actions', 'corrective_records', 'batch_holds'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists %I on public.%I', t || '_read', t);
    execute format('create policy %I on public.%I for select to anon, authenticated using (true)', t || '_read', t);
    execute format('revoke insert, update, delete, truncate on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to anon, authenticated', t);
  end loop;
end $$;

-- 5. Realtime: add every table to supabase_realtime (skips tables already in it)
do $$
declare
  t text;
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    create publication supabase_realtime;
  end if;
  foreach t in array array['events', 'incidents', 'relay_trace', 'actions', 'corrective_records', 'batch_holds'] loop
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;

-- 6. Storage: public buckets for keyframes and clips (an existing clips bucket is kept as is)
insert into storage.buckets (id, name, public)
values ('keyframes', 'keyframes', true), ('clips', 'clips', true)
on conflict (id) do nothing;
