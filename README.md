# Household Budget

A private, local budgeting app for a two-person household. It ingests bank data
(via Plaid), tracks budgets, net worth, and financial-goal timelines, detects
recurring subscriptions/bills, and lets you **chat** with your finances — in the
app or from your phones via Telegram — e.g. *"Can we afford a $4,000 vacation,
and how much would it delay the house fund?"*

Everything runs on your machine. The only outbound calls are to **Plaid** (bank
sync) and your local **Claude Code CLI** (the AI, using your Claude subscription —
no API key, no per-token bill).

## Quick start

### Try it in 2 minutes (no accounts, no keys)
You can explore the whole app on realistic fake data before connecting anything.

**Prerequisites:** Windows + [Python](https://www.python.org/downloads/) — built and
tested on **3.14**; recent 3.12/3.13 likely work too. Use the python.org installer so
the `py` launcher is available, and tick *"Add python.exe to PATH"*. Nothing else is
required for this step.

1. **Get the code** — download the ZIP (green *Code* button → *Download ZIP*) and
   unzip it, or clone:
   ```
   git clone https://github.com/Jakemiingl/household-budget-app.git
   cd household-budget-app
   ```
2. **Launch** — double-click **`start.bat`**. The first run creates a virtual
   environment and installs dependencies (a minute or two), then your browser
   opens to http://127.0.0.1:8765. The console window that appears **is** the app —
   minimize it, don't close it.
3. **Load demo data** — go to the **Setup** tab → **Load sample data**. You now have
   ~4 months of a fake household to click through (Dashboard, Budgets, Goals,
   Recurring, Charts, etc.).

No `.env` is needed for this — every setting has a sensible default.

### Connect your own data
When you're ready to use real accounts and the AI assistant:

1. **Copy `.env.example` to `.env`** (it's git-ignored) and fill in what you want —
   see [Setup details](#setup-details) below. You can enable features independently:
   Plaid for banks, the Claude CLI for AI chat, Telegram for phone access.
2. **Restart** `start.bat` to pick up the changes. Real banks connect from the
   **Setup** tab (sandbox works immediately with `user_good` / `pass_good`).

> **Run only one copy at a time.** Two instances fight over the Telegram bot (you'll
> see it "work for one person but not the other"). If you restart during use, make
> sure the previous console window is closed first.

## Features
- **Accounts** — Plaid-synced accounts plus **manual accounts** for banks Plaid
  can't connect; update manual balances anytime. All feed net worth.
- **Budgets** — monthly limits vs. actuals, or let the **AI build a budget** for you.
- **Goals** — projected completion dates from your real surplus; simulate how a
  purchase delays each goal. AI can propose goals too.
- **Recurring** — auto-detected subscriptions/bills with next-due dates, monthly
  cost, and totals; dismiss ones you cancel (they reappear if charged again).
- **Transactions & Rules** — self-building categorization: tag an uncategorized
  charge and turn it into a keyword rule that re-categorizes past + future matches;
  AI suggests categories for you.
- **Assistant** — ask money questions and get budget/goal proposals you apply in
  one click. Available in-app and via **Telegram** from your phones.

## Setup details
See the in-app **Setup** tab for step-by-step instructions and live status, plus
`.env.example` for all configuration. Summary:

- **Plaid** (bank sync): get free keys at https://dashboard.plaid.com → put
  `PLAID_CLIENT_ID` + secret in `.env`. Start in `sandbox` (fake banks,
  `user_good`/`pass_good`). For real banks, get **Production** access, then set
  `PLAID_ENV=production` and use the **Production** secret.
- **AI**: uses the `claude` CLI already installed, default model `haiku`
  (`CLAUDE_MODEL` in `.env`). No API key needed.
- **Telegram** (optional phone chat): create a bot via @BotFather, put the token
  in `TELEGRAM_BOT_TOKEN`, message the bot to learn your chat ID, then list IDs in
  `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated).

## Status
Fully working with Plaid **Production** (live since 2026-06-15; a real bank is
connected, `PLAID_ENV=production`). Sandbox still works for testing.

## For developers / future Claude
See **`CLAUDE.md`** for full architecture, data model, the LLM/Plaid/Telegram
design, and gotchas. `data/` (database, logs) and `.env` are git-ignored.
