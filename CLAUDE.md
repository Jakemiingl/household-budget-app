# CLAUDE.md — Household Budget app

Project context for any Claude working on this codebase. Read this first.

## What this is
A **private, local desktop budgeting app** for a two-person household. It ingests
bank data via Plaid, tracks budgets / net worth / goals, detects recurring charges,
and provides an AI assistant you can chat with (in-app **and** from your phone via
Telegram) to ask things like "Can we afford a $4,000 vacation, and how would it
delay our goals?"

Everything runs locally on your Windows 11 PC. The only outbound calls are to
**Plaid** (bank sync) and the **local Claude Code CLI** (the AI, via your Claude
*subscription* — no API key, no per-token billing).

## Project status
- **Feature-complete and working end-to-end** — every feature in the Features
  section below is built and tested.
- Runs locally on Windows: Python + FastAPI backend, a vanilla-JS SPA frontend.
  The only outbound calls are to Plaid (bank sync) and the local Claude Code CLI.
- **Plaid** works in **sandbox** (free, fake banks) out of the box; **production**
  is supported once your Plaid account is approved — set `PLAID_ENV=production` and
  your production secret in `.env`. NOTE: the Plaid **Data Transparency** use case
  must be configured in the Plaid Dashboard or Hosted Link exits early.
- The **Telegram bot is optional**: add a bot token + your allowed chat ID(s) to
  `.env` to chat with the assistant from your phone; leave them blank to disable it.
- All secrets live in `.env` (git-ignored). Copy `.env.example` to `.env` to start.

## How to run
- Normal use: double-click `start.bat` (or the "Household Budget" desktop
  shortcut). It creates the venv on first run, starts the server, launches the
  Telegram bot, and opens http://127.0.0.1:8765.
- Dev/manual: `./.venv/Scripts/python.exe -m app.main`
- **RUN ONLY ONE INSTANCE.** Two instances both poll Telegram and fight over
  messages (Telegram allows one getUpdates consumer; the loser gets HTTP 409).
  Symptom we hit: "works for one spouse, not the other." When restarting during
  dev, kill ALL app.main processes first:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -like '*app.main*' } | Stop-Process -Force`
  Note the venv `python.exe` launcher spawns parent+child (2 processes per
  instance) — that's normal; only the child (port 8765 owner) polls.

## Stack
Python 3.14 + FastAPI + SQLite (`data/budget.db`) + vanilla-JS SPA (`web/`).
Deps in `requirements.txt`: fastapi, uvicorn, python-dotenv, httpx,
plaid-python (v40), pydantic, pydantic-settings. All install cleanly on 3.14.
No pandas (math is plain SQL/Python) to keep deps light.

## Architecture & file map
```
app/
  main.py          FastAPI app; lifespan (init_db + start Telegram bot); routers; serves web/
  config.py        Settings from .env (Plaid, Claude CLI, Telegram, app host/port)
  db.py            SQLite schema (CREATE IF NOT EXISTS) + seed categories/rules/member
  plaid_client.py  Plaid SDK: hosted link, exchange, accounts, transactions_sync, sandbox
  budget_engine.py categorize_with_rule() (sign-aware via direction_matches), net_worth
                   (+real_assets breakdown), accounts_summary (+equity & is_stale/stale_days),
                   monthly_summary, budget_vs_actual, cash_flow, category_averages,
                   average_monthly_surplus, recategorize_all, pfc_to_category_name(),
                   debt_payoff_plan() (avalanche: liabilities ranked by APR desc,
                   monthly/annual interest, unrated last).
                   NOTE: category totals NET all txns by kind (a refund/money-in in an
                   expense category reduces it; off-sign no longer vanishes).
  goal_engine.py   project() (priority-waterfall; uses live account balances + marks
                   completion; payoff goals w/ a bound-account APR project via
                   amortization _payoff_months() → realistic date + interest-to-clear),
                   simulate_purchase, bound_state() (payoff/savings derive; blends APR)
  recurring.py     detect() recurring/subscription charges (cadence + amount analysis)
  llm_client.py    Claude CLI shell-out: _call(), complete() (chat), suggest_categories()
  assistant.py     SHARED pipeline: build_context() + respond(); used by web chat AND telegram
  telegram_bot.py  Long-poll bot (run_bot), format_reply(); allowlist security
  sample_data.py   load() a realistic fake household (~4 months) for demos/testing
  routers/
    accounts.py     GET list (incl. net worth); GET /debt-plan (avalanche); POST /manual;
                    PATCH/DELETE {id} (manual-only); PATCH {id}/rate (APR, any credit/loan
                    incl. Plaid)
    transactions.py GET list (paginated: limit/offset, returns total), /uncategorized
                    (incl. PFC hint), /mismatches (PFC kind-level), /categories;
                    POST /recategorize, /categories (create), {id}/ignore-mismatch;
                    PATCH {id}/category
    budgets.py      GET "" (vs actual + this-month surplus), /summary, /cash-flow; PUT/DELETE {category_id}
    goals.py        GET ""; POST "" / PUT {id} (accept account_ids to bind/snapshot); DELETE {id}
    rules.py        GET rules; POST "" (create, w/ direction); PUT {id} (edit, re-cat);
                    DELETE {id} (re-cat affected); POST {id}/mute-pfc; POST /suggest (AI)
    recurring.py    GET ""; POST /dismiss, /restore; GET /dismissed
    plaid.py        POST /sandbox-connect, /hosted-link, /hosted-link/poll, /sync
    chat.py         POST "" (assistant.respond); GET /health
    dev.py          POST /load-sample, /reset
web/
  index.html       SPA shell; tabs: Dashboard, Accounts, Budgets, Goals, Recurring,
                   Transactions, Rules, Assistant, Setup
  app.js           All frontend logic (one file, vanilla JS, no build step)
  styles.css       Dark theme
data/               budget.db, server.log, _*.json — all git-ignored
```

## Data model (SQLite, see db.py)
- `members` — single shippable "Household" (member_id columns kept on accounts/txns
  so per-person tracking can be added later WITHOUT migration; currently NOT split
  by person per user request).
- `plaid_items` — connected Plaid items (item_id, access_token, cursor).
- `accounts` — id (Plaid account_id OR "man_<uuid>"), item_id (NULL = manual),
  type (depository/credit/loan/investment/**asset**), current_balance, etc.
  **is_manual = (item_id IS NULL).** Net worth: type in (credit, loan) = liability;
  everything else (incl. **asset**) = asset. **`linked_account_id`** on an `asset`
  row points at the loan financing it, so accounts_summary can derive per-item equity
  (asset value − loan owed). `type='asset'` = manually-tracked physical things
  (vehicles, RV, real estate) whose value Plaid can't see. **`interest_rate`** = APR %
  on a credit/loan account (NULL = unknown); drives the avalanche debt-payoff plan +
  interest-aware goal projections. Settable on Plaid accounts (sync won't clobber it).
- `transactions` — id (Plaid txn id or sample), amount (**Plaid sign: + = spending,
  − = money in**), category_id, category_rule_id (rule that set it), plaid_category
  (legacy, often NULL), **plaid_pfc** (Plaid personal_finance_category 'detailed'),
  **pfc_ignored** (dismissed a PFC mismatch), pending.
- `categories` (name, kind: income/expense/transfer) + `category_rules`
  (pattern, category_id, priority [user=50, seeded=100], **direction**: any/in/out,
  **pfc_mute**: suppress this rule's PFC mismatch flags). Add categories at runtime
  via POST /api/transactions/categories.
- `budgets` (category_id unique, monthly_limit).
- `goals` (name, target_amount, current_amount, target_date, priority, **completed_at**)
  + **`goal_accounts`** (goal_id, account_id, start_balance) — accounts a goal tracks;
  progress derived from live balances vs. the start snapshot (see goal_engine).
- `recurring_dismissed` (merchant_key, dismissed_at) — see Recurring below.

## Features (all built & tested)
- **Accounts tab** — Plaid accounts (read-only, "Plaid" chip) + **manual accounts**
  for banks Plaid can't reach (add/edit balance/delete; count toward net worth).
  PATCH/DELETE guarded to manual-only (`_require_manual`). **Physical assets**
  (`type='asset'`: vehicle/RV/real-estate/other) — separate "Add a physical asset"
  form; value counts on the asset side of net worth (with an "Owned Assets" card),
  optionally **linked to its financing loan** to show **equity** (value − owed).
  **Staleness reminder**: manual accounts/assets not updated in `STALE_AFTER_DAYS`
  (90) get a "stale Nd" chip + a banner on the Dashboard and Accounts tabs (manual
  balances don't auto-update like Plaid's). **APR per credit/loan** (inline editable,
  Plaid rows too) + a **Debt payoff plan** panel (avalanche order, balance/APR/monthly
  interest per debt, total monthly+annual interest bleed, unrated debts flagged last).
  Added 2026-06-24.
- **Budgets** — monthly limit per category; budget-vs-actual with bars; **this-month
  surplus** card (income − expenses) top-left; **inline edit** the limit + delete per
  row. "✨ Build my budget with AI" → assistant proposes budgets you apply in one click.
- **Goals** — priority-waterfall projections from average monthly surplus; **purchase
  simulation** via the assistant ("how much does $X delay each goal?"); **inline edit**
  (name/target/saved/date/priority) + delete per goal.
  "✨ Plan a goal with AI" → assistant proposes goals.
  **Account-bound goals**: bind a goal to one+ accounts (checkboxes in add/edit forms)
  and progress tracks automatically from live balances. All-liability accounts →
  **pay-off** mode (progress = paid-down since the start snapshot; auto-completes at
  $0 owed); else **savings** mode (combined balance toward target). Completion is
  sticky (`completed_at`) and completed goals drop out of the surplus waterfall.
  Binding snapshots the balance AT BIND TIME as the start, so progress is measured
  from then, not the original debt. **Interest-aware pay-off**: if the bound loan(s)
  have an APR, the projection amortizes (balance accrues interest while paid down) for
  a realistic date and shows total **interest-to-clear**; with no APR it falls back to
  flat remaining/surplus and nudges you to add a rate.
- **Recurring** — auto-detects subscriptions/bills (cadence + amount); shows next
  due + monthly-equivalent + totals; auto-marks inactive after ~1.5 missed cycles;
  manual **dismiss/restore** (resurfaces if charged again after dismissal date).
- **Transactions** — list (**paginated**, 100/page with Prev/Next + "X–Y of N"),
  edit category, re-run categorization. Search spans the last 6 months.
- **Rules** — self-building categorization: tag uncategorized txns; creating a
  keyword rule **retroactively re-categorizes all matching txns**; "✨ Suggest
  categories with AI" pre-fills category+keyword per uncategorized txn.
  **Sign-aware** ("Applies to": any / money-in / money-out) so one keyword can route
  by direction (e.g. *mercury* → Debt Payment when out, Income when in). **Standalone
  add-rule + add-category forms** (no uncategorized txn needed). **Edit AND delete
  re-categorize** the affected txns. **PFC rule-mismatches**: flags rules whose
  category *kind* disagrees with Plaid's PFC (kind-level only, to stay high-signal);
  per-row **Ignore** (`pfc_ignored`) or per-rule **Mute** (`pfc_mute`). The *paypal*
  and *deposit* rules are intentionally muted (see [[session-2026-06-23-rules-pfc-goals]]).
- **PFC pre-fill** — the Uncategorized queue shows Plaid's `personal_finance_category`
  as a hint and pre-selects the mapped category (PFC is HINT-ONLY; user keyword rules
  always win for actual categorization). Crucial for masked cards (some issuers send
  transaction descriptions as asterisks), where PFC is the only categorization signal.
- **Assistant** — in-app chat + Telegram. Answers affordability/budget questions
  AND returns one-click-apply `proposed_budgets` / `proposed_goals`. Money math is
  always computed in Python (assistant.build_context); the LLM only narrates.
- **Setup** — load sample data / reset; Plaid connect (sandbox + Hosted Link);
  Telegram + Plaid setup instructions and status chips.

## AI design (IMPORTANT)
- LLM = **local Claude Code CLI via subscription**, NOT the API. `llm_client._call()`
  runs `claude -p --model <CLAUDE_MODEL> --output-format json`, prompt piped via
  stdin (shell=True), parses the JSON envelope's `result`.
- Default model **`haiku`** (light on subscription limits). Configurable in `.env`.
- The math is deterministic Python; the model only reasons over provided figures,
  so answers can't drift. To swap to OpenAI/Anthropic API later, reimplement only
  `llm_client._call()`.

## Plaid notes
- Sandbox test login: `user_good` / `pass_good`. Instant fake bank: ins_109508.
- **Hosted Link** (not embedded Link) is used for real banks: app calls
  `/api/plaid/hosted-link` → opens `secure.plaid.com/hl/...` → user logs in on
  Plaid's domain (handles OAuth) → app polls `/hosted-link/poll`. Chosen so a
  localhost app needn't register an HTTPS OAuth redirect. No completion_redirect_uri
  set (Plaid shows its own done screen).
- First sync after connecting returns 0 txns (Plaid prepares async) →
  `_initial_sync()` retries ~6×/3s.
- We capture Plaid's **`personal_finance_category`** (`plaid_pfc`), not the deprecated
  legacy `category` (often NULL). PFC arrives nested; extract `.detailed`. Some banks
  redact in-store merchant names to asterisks → PFC is the only signal we have for them.

## Telegram
- `telegram_bot.run_bot()` long-polls (outbound only; no inbound/tunnel needed).
- Security: `TELEGRAM_ALLOWED_CHAT_IDS` allowlist. Unknown senders get their chat
  ID (pairing) but no data. Empty allowlist = pairing mode (no data served).
- LLM call runs via `asyncio.to_thread`. Started in main.py lifespan if token set.

## Gotchas / lessons learned
- `claude` CLI `--json-schema` flag silently outputs nothing in v2.1.x → DON'T use
  it; we instruct JSON in the prompt and parse. Model alias is `haiku` (not `haiku-4-5`).
- `PLAID_ENV=production` + a Sandbox secret → `INVALID_API_KEYS`. Production needs
  approval AND the separate Production secret.
- Two app instances → Telegram 409 / split messages. Run one (see How to run).
- Sample-data accounts have NULL item_id so they read as `is_manual` — fine (demo).
- Amount sign is Plaid's convention everywhere (+ spending, − income).

## Dev tips
- `POST /api/dev/load-sample` (fake household) and `POST /api/dev/reset` make all
  features testable with no Plaid/keys.
- Server log: `data/server.log`. Status: `GET /api/status`.
- When testing the assistant/telegram, the LLM call takes a few seconds (haiku).

## What's NOT done / possible next steps
- Optional/ideas discussed but not built: spending alerts, "cancel this
  subscription" suggestions, dashboard charts, CSV export, stale-balance reminders
  for manual accounts, manual transactions, net-worth-over-time, per-person tracking.
