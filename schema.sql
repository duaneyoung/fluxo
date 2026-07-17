-- Fluxo — Supabase schema
-- Run this in the Supabase SQL Editor (SQL Editor -> New query -> paste -> Run)

-- ============ TRANSACTIONS ============
create table if not exists transactions (
    id               bigint generated always as identity primary key,
    date             date not null,
    is_one_off       boolean default false,
    transaction_type text not null,               -- 'inflow' | 'outflow'
    amount           numeric not null default 0,  -- always stored positive
    category_1       text not null,
    category_2       text,
    category_3       text,
    method           text,
    details          text,
    created_at       timestamptz default now()
);

create index if not exists idx_transactions_date on transactions (date);
create index if not exists idx_transactions_type on transactions (transaction_type);

-- ============ SETTINGS (single-row, id = 1) ============
-- Stores the user's preferred currency and their custom 3-level category tree.
create table if not exists settings (
    id                 bigint primary key default 1,
    currency           text default 'EUR',
    category_hierarchy jsonb default '{}'::jsonb,
    updated_at         timestamptz default now(),
    constraint settings_singleton check (id = 1)
);

-- Seed the singleton row if it doesn't exist. category_hierarchy is populated
-- by the app on first load if left empty (see db.DEFAULT_HIERARCHY).
insert into settings (id, currency, category_hierarchy)
values (1, 'EUR', '{}'::jsonb)
on conflict (id) do nothing;
