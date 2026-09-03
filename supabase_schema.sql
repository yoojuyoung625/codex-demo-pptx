create table if not exists public.keyword_records (
  date date not null,
  keyword text not null,
  type text not null check (type in ('brand', 'nonbrand')),
  status text not null,
  estimated_query bigint,
  attempts integer not null default 1,
  calculation_mode text,
  finalized_at timestamptz,
  updated_at timestamptz,
  error text,
  calculation jsonb,
  snapshot jsonb,
  primary key (date, keyword)
);

create table if not exists public.collection_jobs (
  id text primary key,
  target_date date,
  started_at timestamptz not null,
  finished_at timestamptz,
  status text not null,
  requested integer default 0,
  final_count integer default 0,
  failed_count integer default 0,
  payload jsonb
);

alter table public.keyword_records enable row level security;
alter table public.collection_jobs enable row level security;

-- 앱은 서버 측 Secrets의 service_role 키로만 접근합니다.
-- anon/authenticated 공개 정책은 생성하지 않습니다.

