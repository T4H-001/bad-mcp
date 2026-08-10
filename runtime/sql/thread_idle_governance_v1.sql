-- THREAD-IDLE-001 governed persistence, qualification target: Tech4Humaninty Supabase.
-- Private schema, service-role only. Apply via governed migration tooling.

create table if not exists governance.thread_activity_register (
  thread_key text primary key,
  source_system text not null,
  source_native_id text not null,
  source_ref text,
  content_revision text not null,
  content_hash text not null,
  first_seen_at timestamptz not null default now(),
  last_message_at timestamptz not null,
  last_material_activity_at timestamptz not null,
  priority text not null default 'normal' check (priority in ('hot','normal')),
  idle_candidate_at timestamptz,
  idle_confirmed_at timestamptz,
  state text not null default 'ACTIVE' check (state in ('ACTIVE','IDLE_CANDIDATE','PROCESSING','PERSISTED','ROUTED','BLOCKED','QUIESCENT','SUPERSEDED')),
  observer_id text,
  observer_checkpoint jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique (source_system, source_native_id)
);

create table if not exists governance.thread_closeouts (
  closeout_id uuid primary key default gen_random_uuid(),
  work_key text not null unique,
  thread_key text not null references governance.thread_activity_register(thread_key) on update cascade on delete restrict,
  source_revision text not null,
  source_hash text not null,
  snapshot_ref text,
  intent text,
  classification text not null check (classification in ('REAL','PARTIAL','BLOCKED','DEGRADED','QUARANTINED','REFERENCE')),
  decisions jsonb not null default '[]'::jsonb,
  evidence_refs jsonb not null default '[]'::jsonb,
  assets jsonb not null default '[]'::jsonb,
  blockers jsonb not null default '[]'::jsonb,
  next_executable_actions jsonb not null default '[]'::jsonb,
  canonical_destinations jsonb not null default '[]'::jsonb,
  receipt jsonb not null default '{}'::jsonb,
  compiler_version text not null default 'outcome-001/v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists governance.thread_outcomes (
  outcome_id uuid primary key default gen_random_uuid(),
  closeout_id uuid not null references governance.thread_closeouts(closeout_id) on delete cascade,
  outcome_key text not null unique,
  verb text not null,
  object text not null,
  desired_state text not null,
  business_value text,
  success_conditions jsonb not null default '[]'::jsonb,
  constraints jsonb not null default '[]'::jsonb,
  required_capabilities jsonb not null default '[]'::jsonb,
  owner_worker text,
  state text not null check (state in ('DISCOVERED','ADMITTED','CLAIMED','EXECUTING','VERIFYING','REAL','BLOCKED','QUARANTINED','KILLED')),
  dependencies jsonb not null default '[]'::jsonb,
  checkpoint jsonb not null default '{}'::jsonb,
  latest_delta text,
  next_executable_action text,
  evidence jsonb not null default '[]'::jsonb,
  receipt jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists thread_activity_last_material_idx on governance.thread_activity_register(last_material_activity_at);
create index if not exists thread_activity_state_idx on governance.thread_activity_register(state);
create index if not exists thread_closeouts_thread_idx on governance.thread_closeouts(thread_key, created_at desc);
create index if not exists thread_outcomes_closeout_idx on governance.thread_outcomes(closeout_id, state);

alter table governance.thread_activity_register enable row level security;
alter table governance.thread_closeouts enable row level security;
alter table governance.thread_outcomes enable row level security;
grant usage on schema governance to service_role;
grant select, insert, update, delete on governance.thread_activity_register, governance.thread_closeouts, governance.thread_outcomes to service_role;
revoke all on governance.thread_activity_register, governance.thread_closeouts, governance.thread_outcomes from anon, authenticated;

create or replace function public.t4h_thread_activity_upsert(p jsonb)
returns jsonb language plpgsql security invoker
set search_path = pg_catalog, public, governance
as $$
declare v_key text := p->>'thread_key'; v_row governance.thread_activity_register;
begin
  if v_key is null or p->>'source_system' is null or p->>'source_native_id' is null or p->>'content_revision' is null or p->>'content_hash' is null then
    raise exception 'missing required thread activity fields';
  end if;
  insert into governance.thread_activity_register(thread_key,source_system,source_native_id,source_ref,content_revision,content_hash,first_seen_at,last_message_at,last_material_activity_at,priority,state,observer_id,observer_checkpoint,updated_at)
  values(v_key,p->>'source_system',p->>'source_native_id',p->>'source_ref',p->>'content_revision',p->>'content_hash',coalesce((p->>'first_seen_at')::timestamptz,now()),(p->>'last_message_at')::timestamptz,(p->>'last_material_activity_at')::timestamptz,coalesce(p->>'priority','normal'),coalesce(p->>'state','ACTIVE'),p->>'observer_id',coalesce(p->'observer_checkpoint','{}'::jsonb),now())
  on conflict(thread_key) do update set source_ref=excluded.source_ref,content_revision=excluded.content_revision,content_hash=excluded.content_hash,last_message_at=excluded.last_message_at,last_material_activity_at=excluded.last_material_activity_at,priority=excluded.priority,state=excluded.state,observer_id=excluded.observer_id,observer_checkpoint=excluded.observer_checkpoint,updated_at=now()
  returning * into v_row;
  return jsonb_build_object('thread_key',v_row.thread_key,'content_revision',v_row.content_revision,'state',v_row.state,'updated_at',v_row.updated_at);
end; $$;

create or replace function public.t4h_thread_closeout_persist(p jsonb)
returns jsonb language plpgsql security invoker
set search_path = pg_catalog, public, governance
as $$
declare v_closeout governance.thread_closeouts; v_outcome jsonb; v_outcome_count integer := 0;
begin
  insert into governance.thread_closeouts(work_key,thread_key,source_revision,source_hash,snapshot_ref,intent,classification,decisions,evidence_refs,assets,blockers,next_executable_actions,canonical_destinations,receipt,compiler_version,updated_at)
  values(p->>'work_key',p->>'thread_key',p->>'source_revision',p->>'source_hash',p->>'snapshot_ref',p->>'intent',p->>'classification',coalesce(p->'decisions','[]'::jsonb),coalesce(p->'evidence_refs','[]'::jsonb),coalesce(p->'assets','[]'::jsonb),coalesce(p->'blockers','[]'::jsonb),coalesce(p->'next_executable_actions','[]'::jsonb),coalesce(p->'canonical_destinations','[]'::jsonb),coalesce(p->'receipt','{}'::jsonb),coalesce(p->>'compiler_version','outcome-001/v1'),now())
  on conflict(work_key) do update set snapshot_ref=excluded.snapshot_ref,intent=excluded.intent,classification=excluded.classification,decisions=excluded.decisions,evidence_refs=excluded.evidence_refs,assets=excluded.assets,blockers=excluded.blockers,next_executable_actions=excluded.next_executable_actions,canonical_destinations=excluded.canonical_destinations,receipt=excluded.receipt,compiler_version=excluded.compiler_version,updated_at=now()
  returning * into v_closeout;
  for v_outcome in select value from jsonb_array_elements(coalesce(p->'outcomes','[]'::jsonb)) loop
    insert into governance.thread_outcomes(closeout_id,outcome_key,verb,object,desired_state,business_value,success_conditions,constraints,required_capabilities,owner_worker,state,dependencies,checkpoint,latest_delta,next_executable_action,evidence,receipt,updated_at)
    values(v_closeout.closeout_id,v_outcome->>'outcome_key',v_outcome->>'verb',v_outcome->>'object',v_outcome->>'desired_state',v_outcome->>'business_value',coalesce(v_outcome->'success_conditions','[]'::jsonb),coalesce(v_outcome->'constraints','[]'::jsonb),coalesce(v_outcome->'required_capabilities','[]'::jsonb),v_outcome->>'owner_worker',v_outcome->>'state',coalesce(v_outcome->'dependencies','[]'::jsonb),coalesce(v_outcome->'checkpoint','{}'::jsonb),v_outcome->>'latest_delta',v_outcome->>'next_executable_action',coalesce(v_outcome->'evidence','[]'::jsonb),coalesce(v_outcome->'receipt','{}'::jsonb),now())
    on conflict(outcome_key) do update set owner_worker=excluded.owner_worker,state=excluded.state,dependencies=excluded.dependencies,checkpoint=excluded.checkpoint,latest_delta=excluded.latest_delta,next_executable_action=excluded.next_executable_action,evidence=excluded.evidence,receipt=excluded.receipt,updated_at=now();
    v_outcome_count := v_outcome_count + 1;
  end loop;
  return jsonb_build_object('closeout_id',v_closeout.closeout_id,'work_key',v_closeout.work_key,'outcomes',v_outcome_count,'classification',v_closeout.classification);
end; $$;

revoke all on function public.t4h_thread_activity_upsert(jsonb) from public, anon, authenticated;
revoke all on function public.t4h_thread_closeout_persist(jsonb) from public, anon, authenticated;
grant execute on function public.t4h_thread_activity_upsert(jsonb) to service_role;
grant execute on function public.t4h_thread_closeout_persist(jsonb) to service_role;
