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

-- ============ NET WORTH ASSETS (added later — run this block if upgrading) ============
-- Manual holdings: stocks (ticker+shares) and crypto wallets (BTC amount or address).
create table if not exists net_worth_assets (
    id          bigint generated always as identity primary key,
    kind        text not null,          -- 'stock' | 'crypto'
    label       text not null,          -- ticker (Stooq format, e.g. AAPL.US) or wallet name
    quantity    numeric default 0,      -- shares / BTC amount
    address     text,                   -- optional BTC address for live balance lookup
    created_at  timestamptz default now()
);

-- ============ NET WORTH HISTORY (daily snapshots) ============
create table if not exists net_worth_history (
    snapped_on    date primary key,
    markets       numeric default 0,
    crypto        numeric default 0,
    collectibles  numeric default 0,
    other         numeric default 0,
    total         numeric default 0
);
