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
1. Double-click **`start.bat`** (or the **"Household Budget"** desktop shortcut).
   First run sets up the environment; then your browser opens to
   http://127.0.0.1:8765.
2. To explore instantly with no setup: **Setup → Load sample data**.
3. **Run only one copy at a time** (two will make the Telegram bot misbehave). The
   console window that opens is the app — minimize it, but don't close it.

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
