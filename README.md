# Fluxo — Personal Finance Tracker

A personal web app to track inflows/outflows against a custom 3-level category tree,
with a dashboard (cumulative-spend chart + month comparison), CSV import/export, and
a configurable category manager. Built to match the MoneyBlox CSV format.

> **How to resume work on this project:** In a new Kiro chat, reference this file with
> `#README.md` (and optionally `#app.py`, `#db.py`) to give the assistant full context.

---

## What it does

- **Dashboard:** inflow / outflow / net savings / investments stats for a selected
  timeframe; a cumulative daily-spend line chart comparing the current month against
  chosen previous months, with a ranking badge ("#2 of 6 as of day 17"); spending-by-
  category breakdown; and a recent-transactions list. Filters (exclude investments,
  exclude RSUs, exclude one-offs, timeframe) apply consistently across all sections.
- **Transactions:** full searchable/filterable table, per-row edit/delete, CSV export.
- **Add transaction:** manual form with an inline calendar, quick-category buttons,
  cascading category_1 → category_2 → category_3 selects, payment method, notes, and a
  one-off toggle. Plus a **file-upload tab** for bulk CSV import (MoneyBlox format).
- **Settings:** currency selector + a hierarchical category-tree manager.

## Architecture

- **Backend:** Python + Flask (`app.py`)
- **Data layer:** `db.py` — talks to **Supabase** (hosted Postgres)
- **Frontend:** server-rendered Jinja templates (`templates/`) + `static/style.css`
  (aurora-glass dark theme, indigo/purple gradients). Charts via Chart.js (CDN).
- **Source of truth:** the Supabase database. CSV is an *export/import*, not the live store.
- **Hosting:** Render (free tier), auto-deploys on every git push to `main`.

> **Design note:** this mirrors the CardVault stack (Flask + Supabase + server-rendered
> templates) rather than the React/shadcn/recharts stack in the original spec, so it's
> maintainable the same way as the business app.

## Data model — `transactions`

| column | type | notes |
|--------|------|-------|
| `date` | date | required |
| `is_one_off` | boolean | marks a non-recurring one-off |
| `transaction_type` | text | `inflow` or `outflow` |
| `amount` | numeric | always stored positive |
| `category_1/2/3` | text | 3-level category path (2 & 3 optional) |
| `method` | text | payment method |
| `details` | text | optional notes |

Settings live in a single-row `settings` table: `currency` + `category_hierarchy`
(a JSONB tree of `{ category_1: { category_2: [category_3, ...] } }`).

## Run locally

```powershell
cd moneyblox
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5001
```

Requires a `.env` file with `SUPABASE_URL` and `SUPABASE_KEY` (copy from `.env.example`).

## First-time setup

1. Create a Supabase project (dashboard.supabase.com).
2. In the SQL Editor, paste and run `schema.sql`.
3. Copy `.env.example` → `.env` and fill in `SUPABASE_URL` / `SUPABASE_KEY`
   (Settings → API Keys → use the service role / secret key).
4. Run locally (above), or deploy to Render.
5. Import your history: Transactions → Add → **Upload File**, drop in a MoneyBlox CSV
   (e.g. `revolut_july_moneyblox.csv`). The `Merchant` column, if present, is read into
   `details`.

## Deploy / redeploy (Render)

Render auto-deploys when you push to GitHub:

```powershell
git add .
git commit -m "your change"
git push
```

Set `SUPABASE_URL` and `SUPABASE_KEY` in the Render dashboard → Environment.

## File structure

```
moneyblox/
  app.py                 # Flask routes
  db.py                  # Supabase data access + analytics + constants
  schema.sql             # DB tables (run once in Supabase SQL editor)
  requirements.txt
  render.yaml            # Render deployment config
  .python-version        # pins Python 3.12
  .env / .env.example    # credentials (real / template)
  static/
    style.css            # aurora-glass styling (+ mobile)
  templates/
    base.html            # sidebar layout
    dashboard.html       # stats + chart + breakdown + recent
    transactions.html    # searchable table + export
    add_transaction.html # manual form + inline calendar + CSV upload
    settings.html        # currency + category-tree manager
```

## Known limitations / TODO

- **No authentication.** Single-user model like CardVault — the Render URL is public,
  so anyone with it can view/edit. Add a login before sharing (Supabase Auth, or a
  shared password). The original spec's per-user row-level security was intentionally
  dropped to match the business app.
- PWA icons (`/static/icon-192.png`, `icon-512.png`) are referenced in the manifest but
  not included yet — add them for a proper installable icon.
- Render free tier sleeps after ~15 min idle; first load then takes 30–60s to wake.
