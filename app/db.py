"""SQLite storage. A single local file at data/budget.db — your data never
leaves this machine.

Notes on conventions:
- Money is stored as REAL in the account's currency.
- Transaction `amount` follows Plaid's sign convention: POSITIVE = money out
  of the account (spending), NEGATIVE = money in (income/deposits/refunds).
  The budget engine normalizes this into income vs. expense.
- Account `type` of 'credit' or 'loan' is treated as a liability for net worth.
"""
import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS plaid_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           TEXT NOT NULL UNIQUE,
    access_token      TEXT NOT NULL,
    institution_name  TEXT,
    member_id         INTEGER REFERENCES members(id),
    cursor            TEXT,              -- Plaid transactions/sync cursor
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id                TEXT PRIMARY KEY,  -- Plaid account_id
    item_id           TEXT REFERENCES plaid_items(item_id),
    member_id         INTEGER REFERENCES members(id),
    name              TEXT,
    official_name     TEXT,
    type              TEXT,              -- depository | credit | loan | investment | asset
    subtype           TEXT,
    mask              TEXT,
    current_balance   REAL,
    available_balance REAL,
    currency          TEXT DEFAULT 'USD',
    linked_account_id TEXT REFERENCES accounts(id),  -- asset → the loan financing it (for equity)
    interest_rate     REAL,              -- APR % on a credit/loan account (e.g. 19.99); NULL = unknown
    monthly_payment   REAL,              -- typical $/mo paid toward a credit/loan; powers payoff/interest math
    updated_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    kind  TEXT NOT NULL DEFAULT 'expense'  -- income | expense | transfer
);

CREATE TABLE IF NOT EXISTS category_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,           -- case-insensitive substring match on name/merchant
    category_id INTEGER NOT NULL REFERENCES categories(id),
    priority    INTEGER NOT NULL DEFAULT 100,
    direction   TEXT NOT NULL DEFAULT 'any',  -- any | in | out (in = money in/amount<0, out = money out/amount>0)
    pfc_mute    INTEGER NOT NULL DEFAULT 0    -- 1 = suppress PFC mismatch flags for this rule (intentional you-vs-Plaid difference)
);

-- Saved AI/manual category+keyword suggestions for uncategorized transactions,
-- so the work-in-progress survives closing/refreshing the window. Cleared when
-- the transaction gets categorized (rule applied or set directly).
CREATE TABLE IF NOT EXISTS category_suggestions (
    transaction_id TEXT PRIMARY KEY REFERENCES transactions(id),
    category_id    INTEGER REFERENCES categories(id),
    pattern        TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id            TEXT PRIMARY KEY,      -- Plaid transaction_id
    account_id    TEXT REFERENCES accounts(id),
    date          TEXT NOT NULL,         -- YYYY-MM-DD
    name          TEXT,
    merchant_name TEXT,
    amount        REAL NOT NULL,         -- Plaid sign: + = spending, - = income
    currency      TEXT DEFAULT 'USD',
    plaid_category TEXT,                 -- Plaid legacy category (deprecated; often NULL)
    plaid_pfc     TEXT,                  -- Plaid personal_finance_category 'detailed' (e.g. GENERAL_MERCHANDISE_OTHER...)
    pfc_ignored   INTEGER NOT NULL DEFAULT 0,  -- 1 = user dismissed this row's PFC mismatch
    category_id   INTEGER REFERENCES categories(id),
    category_rule_id INTEGER REFERENCES category_rules(id),  -- rule that set it (NULL = manual/fallback)
    pending       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL UNIQUE REFERENCES categories(id),
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recurring_dismissed (
    merchant_key TEXT PRIMARY KEY,   -- normalized merchant key from recurring.py
    label        TEXT,               -- human-friendly name for display
    dismissed_at TEXT NOT NULL       -- YYYY-MM-DD; charges after this resurface
);

CREATE TABLE IF NOT EXISTS goals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    target_amount REAL NOT NULL,
    current_amount REAL NOT NULL DEFAULT 0,  -- manual progress (unbound goals only)
    target_date   TEXT,                  -- YYYY-MM-DD (optional)
    priority      INTEGER NOT NULL DEFAULT 100,
    completed_at  TEXT,                  -- set once progress first reaches target (sticky)
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Accounts a goal tracks. Progress is derived from these balances vs. the
-- snapshot taken when the account was bound (start_balance). A goal of all
-- liability accounts = debt-payoff; otherwise = savings. See goal_engine.
CREATE TABLE IF NOT EXISTS goal_accounts (
    goal_id       INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    start_balance REAL,                  -- account balance when bound (goal start)
    PRIMARY KEY (goal_id, account_id)
);
"""

# A sensible starter category set so the app is useful immediately.
DEFAULT_CATEGORIES = [
    ("Income", "income"),
    ("Transfer", "transfer"),
    ("Groceries", "expense"),
    ("Dining & Takeout", "expense"),
    ("Housing & Rent", "expense"),
    ("Utilities", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Health", "expense"),
    ("Travel", "expense"),
    ("Subscriptions", "expense"),
    ("Insurance", "expense"),
    ("Debt Payment", "expense"),
    ("Uncategorized", "expense"),
]

# Starter keyword rules -> category name. Keeps first-run categorization useful.
DEFAULT_RULES = [
    ("payroll", "Income"), ("direct dep", "Income"), ("deposit", "Income"),
    ("transfer", "Transfer"),
    ("uber eats", "Dining & Takeout"), ("doordash", "Dining & Takeout"),
    ("restaurant", "Dining & Takeout"), ("starbucks", "Dining & Takeout"),
    ("mcdonald", "Dining & Takeout"),
    ("whole foods", "Groceries"), ("safeway", "Groceries"),
    ("trader joe", "Groceries"), ("grocery", "Groceries"), ("kroger", "Groceries"),
    ("rent", "Housing & Rent"), ("mortgage", "Housing & Rent"),
    ("electric", "Utilities"), ("water", "Utilities"), ("comcast", "Utilities"),
    ("internet", "Utilities"), ("gas company", "Utilities"),
    ("uber", "Transportation"), ("lyft", "Transportation"),
    ("shell", "Transportation"), ("chevron", "Transportation"),
    ("amazon", "Shopping"), ("target", "Shopping"), ("walmart", "Shopping"),
    ("netflix", "Subscriptions"), ("spotify", "Subscriptions"),
    ("hulu", "Subscriptions"), ("disney", "Subscriptions"),
    ("airlines", "Travel"), ("hotel", "Travel"), ("airbnb", "Travel"),
    ("pharmacy", "Health"), ("cvs", "Health"), ("walgreens", "Health"),
    ("insurance", "Insurance"),
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and seed defaults. Safe to call on every startup."""
    with db_cursor() as conn:
        conn.executescript(SCHEMA)

        # Lightweight migration: add transactions.category_rule_id to DBs that
        # predate it (CREATE IF NOT EXISTS won't add columns to existing tables).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
        if "category_rule_id" not in cols:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN category_rule_id INTEGER "
                "REFERENCES category_rules(id)"
            )

        # Lightweight migration: add category_rules.direction to DBs predating it.
        # Existing rules become 'any' (match both signs) — their prior behavior.
        rule_cols = {r["name"] for r in conn.execute("PRAGMA table_info(category_rules)")}
        if "direction" not in rule_cols:
            conn.execute(
                "ALTER TABLE category_rules ADD COLUMN direction TEXT NOT NULL "
                "DEFAULT 'any'"
            )
        # ...and category_rules.pfc_mute (suppress a rule's PFC mismatch flags).
        if "pfc_mute" not in rule_cols:
            conn.execute(
                "ALTER TABLE category_rules ADD COLUMN pfc_mute INTEGER NOT NULL "
                "DEFAULT 0"
            )

        # Lightweight migration: add transactions.plaid_pfc (Plaid's newer
        # personal_finance_category) to DBs predating it. Backfilled separately.
        if "plaid_pfc" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN plaid_pfc TEXT")
        # ...and transactions.pfc_ignored (per-row dismissal of a PFC mismatch).
        if "pfc_ignored" not in cols:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN pfc_ignored INTEGER NOT NULL "
                "DEFAULT 0"
            )

        # Lightweight migration: add goals.completed_at to DBs predating it.
        goal_cols = {r["name"] for r in conn.execute("PRAGMA table_info(goals)")}
        if "completed_at" not in goal_cols:
            conn.execute("ALTER TABLE goals ADD COLUMN completed_at TEXT")

        # Lightweight migration: add accounts.linked_account_id (a physical asset,
        # e.g. a vehicle/RV, points at the loan financing it so we can show equity).
        acct_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
        if "linked_account_id" not in acct_cols:
            conn.execute(
                "ALTER TABLE accounts ADD COLUMN linked_account_id TEXT "
                "REFERENCES accounts(id)"
            )
        # ...and accounts.interest_rate (APR % on credit/loan accounts). Used for
        # avalanche payoff guidance + interest-aware loan-payoff goal projections.
        if "interest_rate" not in acct_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN interest_rate REAL")
        # ...and accounts.monthly_payment ($/mo toward a credit/loan) so the debt
        # plan can amortize real principal paydown instead of assuming a flat balance.
        if "monthly_payment" not in acct_cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN monthly_payment REAL")

        # Seed a single shared household. (The member_id columns on accounts/
        # transactions are kept so per-person tracking can be added later
        # without a migration.)
        if conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0:
            conn.execute("INSERT INTO members(name) VALUES (?)", ("Household",))

        # Seed categories.
        for name, kind in DEFAULT_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, kind) VALUES (?, ?)",
                (name, kind),
            )

        # Seed rules (only if none exist, so user edits aren't clobbered).
        if conn.execute("SELECT COUNT(*) FROM category_rules").fetchone()[0] == 0:
            cat_ids = {
                r["name"]: r["id"]
                for r in conn.execute("SELECT id, name FROM categories")
            }
            for pattern, cat_name in DEFAULT_RULES:
                if cat_name in cat_ids:
                    conn.execute(
                        "INSERT INTO category_rules(pattern, category_id, priority) "
                        "VALUES (?, ?, ?)",
                        (pattern, cat_ids[cat_name], 100),
                    )
