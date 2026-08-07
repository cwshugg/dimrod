# Treasurer — Budget & Spending Analyst

Treasurer integrates with the YNAB (You Need A Budget) API to sync transactions, store them locally, and produce spending analysis summaries. It operates on multiple budgets independently, providing both automated monthly reports and on-demand query capabilities.

## Purpose

* Sync YNAB transactions daily into local SQLite databases (one per budget)
* Expose an Oracle/NLA endpoint for querying spending summaries over arbitrary date ranges
* Automatically generate and push monthly spending reports on the 5th of each month
* Support multiple independent budgets with separate databases and notification channels

## Architecture

Treasurer follows the standard DImROD Service + Oracle pattern:

* `TreasurerService` runs the main loop (daily sync + monthly auto-trigger detection)
* `TreasurerOracle` exposes HTTP and NLA endpoints for on-demand summary queries
* `TransactionDatabase` wraps `lib/db.py`'s `Database` class for transaction persistence (one instance per budget)
* `lib/ynab.py` handles all YNAB API communication

```mermaid
graph TD
    Config["treasurer.yaml"] --> Service["TreasurerService"]
    Service --> YNAB["lib/ynab.py (YNAB API)"]
    Service --> BudgetA["BudgetContext A"]
    Service --> BudgetB["BudgetContext B"]
    BudgetA --> DBA["TransactionDatabase A (SQLite)"]
    BudgetA --> NtfyA["NtfyChannel A"]
    BudgetB --> DBB["TransactionDatabase B (SQLite)"]
    BudgetB --> NtfyB["NtfyChannel B"]
    Service --> Oracle["TreasurerOracle"]
    Oracle --> HTTP["HTTP Endpoints"]
    Oracle --> NLA["NLA Endpoints"]
```

On startup, the service:

1. Parses the config file into `TreasurerConfig`
2. Initializes the `YNAB` client with the configured access token
3. For each configured budget, creates a `BudgetContext` containing:
   - A `TransactionDatabase` instance pointed at the budget's `db_path`
   - An `NtfyChannel` instance for the budget's `ntfy_topic`
   - Metadata (budget_id, name)
4. Starts the Oracle thread
5. Enters the main service loop

## File/Module Structure

```
services/treasurer/
├── treasurer.py          # Main service file: TreasurerConfig, TreasurerService, TreasurerOracle
├── db.py                 # TransactionDatabase, SummaryDatabase classes and schemas
└── treasurer.yaml        # Config file (deployment instance; not committed)
```

### `treasurer.py`

Contains:
- `TreasurerBudgetConfig` — Config class for a single budget definition
- `TreasurerConfig` — Main service config extending `ServiceConfig`
- `BudgetContext` — Runtime object grouping a budget's DB, ntfy channel, and metadata
- `TreasurerService` — Main service class extending `Service`
- `TreasurerOracle` — Oracle class extending `Oracle`

### `db.py`

Contains:
- `TransactionDatabaseConfig` — extends `DatabaseConfig`
- `TransactionDatabase` — extends `Database`, manages `transactions` and `summaries` tables
- Helper functions for SQL query building

## Configuration Schema (YAML)

```yaml
service_name: treasurer
service_log: stdout
msghub_name: YOUR_MSGHUB_NAME
oracle:
  addr: 0.0.0.0
  port: 2370
  log: stdout
  auth_cookie: treasurer_auth
  auth_secret: YOUR_JWT_SECRET_HERE
  auth_users:
  - username: budget_user
    password: budget_pass
    privilege: 0

ynab:
  access_token: YOUR_YNAB_ACCESS_TOKEN

budgets:
- budget_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
  name: "Personal Budget"
  db_path: "./.treasurer_personal.db"
  ntfy_topic: "dimrod-treasurer-personal"
- budget_id: "ffffffff-1111-2222-3333-444444444444"
  name: "Household Budget"
  db_path: "./.treasurer_household.db"
  ntfy_topic: "dimrod-treasurer-household"

sync_hour: 3
```

### Config Fields

**`TreasurerConfig`** extends `ServiceConfig`:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ynab` | `YNABConfig` | Yes | — | YNAB API credentials (access_token) |
| `budgets` | `list[TreasurerBudgetConfig]` | Yes | — | List of budget definitions |
| `sync_hour` | `int` | No | `3` | Hour of the day (0–23) to run the daily sync |

**`TreasurerBudgetConfig`** extends `Config`:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `budget_id` | `str` | Yes | — | YNAB budget UUID |
| `name` | `str` | Yes | — | Human-readable budget name |
| `db_path` | `str` | Yes | — | Path to this budget's SQLite database file |
| `ntfy_topic` | `str` | Yes | — | ntfy.sh topic for this budget's notifications |

## Database Schema

Each budget has its own SQLite database file containing two tables.

### `transactions` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `TEXT` | `PRIMARY KEY` | YNAB transaction ID |
| `date` | `TEXT` | `NOT NULL` | Transaction date (YYYY-MM-DD) |
| `amount` | `REAL` | `NOT NULL` | Amount in currency units (negative = outflow, positive = inflow) |
| `payee_name` | `TEXT` | | Payee/merchant name |
| `category_id` | `TEXT` | | YNAB category UUID |
| `category_name` | `TEXT` | | Human-readable category name |
| `account_name` | `TEXT` | | Account the transaction belongs to |
| `memo` | `TEXT` | | Transaction memo/description |
| `approved` | `INTEGER` | | Whether the transaction is approved (0/1) |
| `cleared` | `TEXT` | | Cleared status (cleared/uncleared/reconciled) |
| `deleted` | `INTEGER` | | Whether the transaction is marked deleted on YNAB (0/1). Normally deleted rows are removed during sync; this flag lets `generate_summary` defensively skip any deleted row that lingers. Defaults to 0. |
| `parent_transaction_id` | `TEXT` | | For a split transaction's subtransaction, the YNAB id of the parent transaction. `NULL` for a normal (non-split) transaction. Enables removing every stored child when a split parent is deleted. |
| `matched_transaction_id` | `TEXT` | | YNAB's `matched_transaction_id`: when a manual entry and its later bank import are **matched** by YNAB, each object references the other via this id. `NULL` when the transaction is not matched. Used to collapse a matched pair to a single counted transaction at summary time (see *Matched-transaction de-duplication* below). |
| `import_id` | `TEXT` | | YNAB's `import_id`: set **only on imported** transactions (`NULL` for a manually-entered one). Used as a tie-breaker to decide which half of a matched pair to keep when both halves share the same categorization status (the manual half, whose `import_id` is `NULL`, is preferred). |
| `synced_at` | `TEXT` | `NOT NULL` | ISO 8601 timestamp of when this record was last synced |

**Indexes:**
- `idx_transactions_date` on `date` — for efficient date-range queries
- `idx_transactions_category` on `category_name` — for efficient category breakdowns

**Storage note:** transactions are persisted via the `Uniserdes` SQLite encoding — the full object is stored in a leading `encoded_obj` blob column, and a subset of fields (`id`, `date`, `amount`, `category_name`, `deleted`, `parent_transaction_id`, `matched_transaction_id`, `import_id`) are also written as *visible* columns so they can be queried directly (e.g. date-range selects and parent-based deletes). The columns above that are not in that visible set live inside `encoded_obj`.

**Schema migration:** the table is created with `CREATE TABLE IF NOT EXISTS`, which never adds columns to an existing database. When new visible columns are introduced (`deleted`, `parent_transaction_id`, `matched_transaction_id`, `import_id`), `TransactionDatabase.init_tables` runs a lightweight, idempotent migration (`_migrate_transactions_columns`) that inspects `PRAGMA table_info(transactions)` and issues `ALTER TABLE ... ADD COLUMN` only for columns that are missing. Existing databases therefore upgrade in place without breaking: pre-existing rows get `deleted = 0`, `parent_transaction_id = NULL`, and `matched_transaction_id = NULL` / `import_id = NULL` (i.e. treated as unmatched, which is safe).

### `summaries` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `TEXT` | `PRIMARY KEY` | SHA-256 hash of budget_id + start_date + end_date |
| `budget_id` | `TEXT` | `NOT NULL` | YNAB budget UUID |
| `start_date` | `TEXT` | `NOT NULL` | Summary period start (YYYY-MM-DD) |
| `end_date` | `TEXT` | `NOT NULL` | Summary period end (YYYY-MM-DD) |
| `total_expenses` | `REAL` | `NOT NULL` | Sum of all negative amounts (stored as positive number) |
| `total_income` | `REAL` | `NOT NULL` | Sum of all positive amounts |
| `category_breakdown` | `TEXT` | `NOT NULL` | JSON object: {"category_name": total_amount, ...} |
| `generated_at` | `TEXT` | `NOT NULL` | ISO 8601 timestamp of when summary was generated |

### `sync_state` Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `key` | `TEXT` | `PRIMARY KEY` | State key (e.g., `"last_sync_date"`, `"server_knowledge:<budget_id>"`) |
| `value` | `TEXT` | `NOT NULL` | State value |

Stores two kinds of per-budget sync state:

* `server_knowledge:<budget_id>` — the YNAB **server-knowledge** value used for delta sync (see below). This is the authoritative sync cursor: it is passed back to YNAB as `last_knowledge_of_server` and only advances after a delta has been durably applied.
* `last_sync_date` — retained for observability (the date of the last successful sync). Delta sync no longer depends on it; it is kept alongside `server_knowledge`, not used to decide what to fetch.

## Transaction Sync — YNAB `server_knowledge` delta sync

Treasurer syncs transactions using YNAB's **delta (server-knowledge) mechanism** rather than a date-based cursor.

**Why:** the earlier implementation used a moving `since_date` cursor — it fetched `get_transactions(budget_id, since_date=last_sync_date)` and then advanced `last_sync_date` to "today". YNAB's `since_date` filters by the transaction's **date field**, so any transaction that later appeared with a date *older* than the cursor — a pending charge that clears days later, a delayed bank import, or a manually back-dated entry — failed `date >= since_date` and was **never re-fetched**, so it never reached the DB and was missing from monthly summaries. (See investigation report `eaf41eb0f3ef2f66`.)

**How delta sync works:**

* Each budget stores a `server_knowledge` integer per budget in `sync_state`.
* On each sync, Treasurer sends the stored value as `last_knowledge_of_server`. YNAB returns **only the entities that were created, modified, or deleted since that value — regardless of transaction date** — plus a new top-level `server_knowledge` to persist for next time.
* Created/modified transactions are upserted (`INSERT OR REPLACE`, primary key = YNAB transaction id). Transactions returned with `deleted: true` are removed from the local `transactions` table so they drop out of summaries. Split transactions are expanded into their subtransactions (and a deleted subtransaction is removed).
    * **Deleted split parents remove their children.** A split transaction is stored only as its subtransaction rows (keyed by each subtransaction id); the parent id is never stored on its own. When YNAB later reports the parent as `deleted: true`, it frequently does **not** re-list the subtransactions (the `subtransactions` array is empty on a deleted parent — deleted entities only appear in delta responses). To avoid orphaning the previously-stored child rows, each stored subtransaction persists a `parent_transaction_id`, and deletion during sync removes **both** the row whose `id` matches the deleted transaction **and** every row whose `parent_transaction_id` matches it (`TransactionDatabase.delete_transaction_and_children`). This is idempotent and retry-safe (removing an absent id is a harmless no-op), and it runs before `server_knowledge` is advanced, preserving the retry invariant.
* The new `server_knowledge` is persisted **only after** the transactions are durably applied. If the fetch fails, or the DB apply fails, the stored `server_knowledge` is **not advanced and not lost** — so the next run retries the exact same delta instead of skipping data.
* **First sync / no stored knowledge:** when there is no `server_knowledge` yet, the request is made with `last_knowledge_of_server=None`, which returns the **full** transaction set. This is also the natural **backfill** path (see below).

> Implementation note: delta sync calls the YNAB SDK's transactions API directly (via `self.ynab.api_transactions().get_transactions(..., last_knowledge_of_server=...)`) rather than the `YNAB.get_transactions()` helper, because the helper does not surface the response's `server_knowledge` and silently drops `deleted` transactions — both of which delta sync needs.

## Matched-transaction de-duplication (imported + manual)

**Problem.** When the user manually logs a transaction in YNAB and the bank's Direct Import later brings in the *real* one, YNAB **matches** the two into a single reconciled charge. In the delta response, YNAB returns **both** transaction objects, cross-referencing each other via `matched_transaction_id`. Treasurer stores both rows, so a naive summary would **count the same real charge twice** and pollute the category breakdown — the imported half is frequently `Uncategorized`, while the manual half carries the user's real category.

**Fix (at summary time).** `generate_summary` collapses each matched pair to exactly one counted transaction via `TreasurerService._find_matched_duplicate_ids`. This runs in the same place as the defensive `deleted` filter, keeping all counting decisions in one pass over the rows. The dropped half is logged as a `(SKIP; Matched duplicate)` debug line (consistent with the existing `(SKIP; ...)` logging).

Selection rules (deterministic and idempotent):

* A row is only collapsible if its `matched_transaction_id` is non-`NULL` **and** the referenced partner row is also present in the same range. If only one half is present (e.g. the partner was deleted/expunged), that half is kept unchanged.
* The **kept** half is chosen by the following priority:
  1. **Categorized over uncategorized.** A row is treated as *categorized* when its `category_name` is truthy **and** not equal (case-insensitive, stripped) to `Uncategorized` **and** its `category_group_name` is not `None`/empty. If **exactly one** half is categorized, that half is kept and the other dropped. The imported half is frequently `Uncategorized`, so preferring the categorized half keeps the charge in the user's real category (rather than distorting the breakdown by landing it in `Uncategorized`).
  2. **Manual over imported.** If both halves tie on rule 1 (both categorized, or both uncategorized), the **manual** half (`import_id` is `NULL`) is kept and the imported one dropped.
  3. **Stable id tie-break.** If still tied (same import status), the row with the lexicographically greater `id` is dropped.

**De-dup is based ONLY on YNAB's explicit match linkage — never on equal date/amount/payee.** This is deliberate: two *value-identical but unlinked* transactions (both `matched_transaction_id = NULL`) represent two genuinely separate real payments (e.g. two identical rent payments in one month) and **must both remain counted**. A value-based de-dup would incorrectly erase one of them, so it is intentionally not used.

Because the fix lives in `generate_summary` and reads the persisted `matched_transaction_id` / `import_id` fields, it does not touch sync/store logic, and it preserves the delta `server_knowledge` retry-safety invariant.

### One-time full re-sync after deploying (backfill)

Because the old moving-date cursor already skipped past some transactions, those rows are missing from the local DB and will **not** come back through a normal incremental delta (they did not "change" recently). The same gap appears whenever a schema change adds new transaction columns (e.g. the matched-transaction linkage `matched_transaction_id` / `import_id`): the migration adds the columns but does **not** backfill them, and an incremental delta only re-fetches rows that changed on the server, so historical rows keep the new columns `NULL`. To recover them, run **one full re-sync** after deploying such a change.

**Preferred: the `full` sync flag.** Trigger a full re-sync via the existing endpoint — no DB surgery, no downtime, and no cursor-loss window:

```jsonc
// POST /sync  — all budgets
{ "full": true }

// POST /sync  — a single budget
{ "budget_name": "Master Budget", "full": true }
```

This ignores the stored `server_knowledge`, fetches the **full** transaction set (`last_knowledge_of_server=None`), re-upserts every row **with the new fields populated**, and then advances `server_knowledge` to the fresh response value. It is safe and idempotent (upserts are keyed by the unique YNAB transaction id) and preserves the retry invariant (`server_knowledge` only advances after a durable apply). Run it **once**; subsequent syncs return to incremental delta automatically. In code this is `sync_budget(ctx, force_full=True)` / `sync_all_budgets(force_full=True)`.

**Automatic on first run.** The first sync after a fresh deploy already does a full fetch, because no `server_knowledge` value exists yet in `sync_state`. This repopulates historical / back-dated transactions with no action required.

**Last resort (not recommended).** Clearing `server_knowledge` also forces the next sync to do a full fetch, but it leaves a window where a crash before the next sync loses the cursor entirely. Prefer the `full` flag above. If you must:

```sql
-- Run against the budget's .treasurer_*.db (service stopped):
DELETE FROM sync_state WHERE key LIKE 'server_knowledge:%';
```

## Service Loop Logic

The service main loop runs continuously with a sleep interval (e.g., 60 seconds between checks).

### Daily Transaction Sync

```
Every iteration of the main loop:
  1. Check current time
  2. If current hour == sync_hour AND not already synced today:
     For each budget in config.budgets:
       a. Read server_knowledge:<budget_id> from sync_state (None => full fetch)
       b. Call the YNAB transactions API with last_knowledge_of_server = that value
          -> returns created/modified/deleted transactions + a new server_knowledge
       c. Resolve category_name via ynab.get_categories() (cached)
       d. Upsert created/modified (INSERT OR REPLACE); delete `deleted: true` rows
       e. Only after a successful apply, persist the new server_knowledge
          (on any fetch/apply error, do NOT advance it -> the delta is retried)
       f. Log success/failure
     Mark today as synced (in-memory flag, resets at midnight)
```

### Monthly Auto-Trigger (5th of Month)

```
Every iteration of the main loop:
  1. Check current date
  2. If day == 5 AND not already triggered this month:
     For each budget in config.budgets:
       a. Calculate previous month's date range:
          - start_date = first day of previous month
          - end_date = last day of previous month
       b. Call self.generate_summary(budget, start_date, end_date)
       c. Store summary in summaries table
       d. Format summary as human-readable message
       e. Send via budget's NtfyChannel
     Mark this month as triggered (in-memory flag, resets on new month)
```

### Loop Pseudocode

```python
def run(self):
    last_sync_day = None
    last_trigger_month = None

    while True:
        now = datetime.now()

        # Daily sync check
        if now.hour == self.config.sync_hour and last_sync_day != now.date():
            self.sync_all_budgets()
            last_sync_day = now.date()

        # Monthly trigger check (5th of month)
        if now.day == 5 and last_trigger_month != (now.year, now.month):
            self.trigger_monthly_summaries()
            last_trigger_month = (now.year, now.month)

        time.sleep(60)
```

## Oracle/NLA Endpoint Definitions

### HTTP Endpoints

#### `GET /summary`

Returns a spending summary for a budget over a date range.

* **Authentication:** Required
* **Request fields:**

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `budget_name` | Yes* | `str` | Human-readable budget name. Resolved case-insensitively by `find_budget_by_name`: an exact (case-insensitive, whitespace-stripped) match wins first; otherwise a case-insensitive substring match resolves the budget **only if exactly one** budget name contains the term (2+ matches is ambiguous → not found). |
| `budget_id` | Yes* | `str` | YNAB budget UUID |
| `start_date` | Yes | `str` | Start of range (YYYY-MM-DD, inclusive) |
| `end_date` | Yes | `str` | End of range (YYYY-MM-DD, inclusive) |

\*One of `budget_name` or `budget_id` must be provided. If both are given, `budget_id` takes precedence.

* **Response (200):**

```json
{
  "success": true,
  "message": "Summary generated.",
  "payload": {
    "budget_name": "Personal Budget",
    "start_date": "2025-01-01",
    "end_date": "2025-01-31",
    "total_expenses": 3245.67,
    "total_income": 5200.00,
    "net": 1954.33,
    "categories": {
      "Groceries": -845.23,
      "Rent/Mortgage": -1500.00,
      "Restaurants": -312.44,
      "Transportation": -188.00,
      "Utilities": -400.00
    },
    "transaction_count": 87
  }
}
```

* **Error (400):** Missing required fields or invalid date format
* **Error (404):** Budget not found

#### `GET /budgets`

Returns a list of all configured budgets.

* **Authentication:** Required
* **Response (200):**

```json
{
  "success": true,
  "payload": {
    "budgets": [
      {"budget_id": "aaaa...", "name": "Personal Budget"},
      {"budget_id": "ffff...", "name": "Household Budget"}
    ]
  }
}
```

#### `POST /sync`

Manually triggers a transaction sync for one or all budgets.

* **Authentication:** Required
* **Request fields:**

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `budget_name` | No | `str` | Sync a specific budget (by name) |
| `budget_id` | No | `str` | Sync a specific budget (by ID) |
| `full` | No | `bool` | When `true`, perform a **full re-sync** (backfill) instead of an incremental delta. Defaults to `false`. `force_full` is accepted as an alias. |

If neither `budget_name` nor `budget_id` is provided, all budgets are synced. The
`full` flag is honored for both the per-budget and all-budgets cases.

**Full re-sync (`{"full": true}`)** ignores the stored `server_knowledge` and asks
YNAB for the **entire** transaction set (`last_knowledge_of_server=None`), then
re-upserts every row and advances `server_knowledge` to the fresh response value —
exactly like a normal sync. Use it as a **one-time backfill** after a schema change
adds new transaction fields (e.g. the matched-transaction linkage
`matched_transaction_id` / `import_id`), because incremental deltas only return rows
that *changed* on the server and therefore never re-fetch unchanged historical rows
to populate the new columns. It is **safe and idempotent**: upserts are keyed by the
unique YNAB transaction id, and — like any sync — `server_knowledge` only advances
after a durable apply, so a crash mid-sync leaves the previous cursor intact and
simply retries. The flag does **not** clear/delete `server_knowledge`; a full fetch
re-populates and re-advances it, which is safer than clearing (no window where a
crash loses the cursor).

* **Response (200):**

```json
{
  "success": true,
  "message": "Synced 142 transactions for 'Personal Budget'."
}
```

When a full re-sync is requested, the message notes it, e.g.
`"Synced 142 transactions for 'Personal Budget' (full re-sync)."` or
`"Synced all budgets (full re-sync)."`.

#### `GET /summaries`

Returns stored historical summaries for a budget.

* **Authentication:** Required
* **Request fields:**

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `budget_name` | Yes* | `str` | Budget name |
| `budget_id` | Yes* | `str` | Budget UUID |
| `limit` | No | `int` | Max summaries to return (default: 12) |

* **Response (200):**

```json
{
  "success": true,
  "payload": {
    "summaries": [
      {
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "total_expenses": 3245.67,
        "total_income": 5200.00,
        "category_breakdown": {"Groceries": -845.23, "...": "..."},
        "generated_at": "2025-02-05T03:15:22Z"
      }
    ]
  }
}
```

### NLA Endpoints

#### `spending_summary`

* **Name:** `spending_summary`
* **Description:** "Get a spending summary for a budget over a date range. Specify the budget name and date range."
* **Handler behavior:**
  1. Parse `budget_name` from the NLA message (substring match against configured budget names)
  2. Extract date range from `extra_params` or parse from natural language (e.g., "last month", "January 2025")
  3. Call the internal `generate_summary()` method
  4. Return an `NLAResult` with the formatted summary

* **NLAResult response:**

```json
{
  "success": true,
  "message": "Personal Budget spending from 2025-01-01 to 2025-01-31: $3,245.67 out, $5,200.00 in. Top categories: Groceries ($845.23), Rent ($1,500.00), Restaurants ($312.44).",
  "message_context": "spending summary",
  "payload": {
    "total_expenses": 3245.67,
    "total_income": 5200.00,
    "categories": {"Groceries": -845.23, "...": "..."}
  }
}
```

#### `list_budgets`

* **Name:** `list_budgets`
* **Description:** "List all configured budgets that can be queried for spending data."
* **Handler behavior:** Return the names and IDs of all configured budgets.

## Key Class and Function Signatures

### `TreasurerBudgetConfig(Config)`

```python
class TreasurerBudgetConfig(Config):
    fields = [
        ConfigField("budget_id",   [str], required=True),
        ConfigField("name",        [str], required=True),
        ConfigField("db_path",     [str], required=True),
        ConfigField("ntfy_topic",  [str], required=True),
    ]
```

### `TreasurerConfig(ServiceConfig)`

```python
class TreasurerConfig(ServiceConfig):
    fields = ServiceConfig.fields + [
        ConfigField("ynab",       [YNABConfig],               required=True),
        ConfigField("budgets",    [list],                     required=True),
        ConfigField("sync_hour",  [int],                      required=False, default=3),
    ]
```

### `BudgetContext`

```python
class BudgetContext:
    """Runtime container for a single budget's resources."""
    def __init__(self, config: TreasurerBudgetConfig):
        self.config = config
        self.db = TransactionDatabase(...)       # initialized from config.db_path
        self.ntfy = NtfyChannel(config.ntfy_topic)
        self.category_cache = {}                  # category_id -> category_name
```

### `TransactionDatabase(Database)`

```python
class TransactionDatabase(Database):
    TABLE_TRANSACTIONS = "transactions"
    TABLE_SUMMARIES = "summaries"
    TABLE_SYNC_STATE = "sync_state"

    def init_tables(self) -> None: ...
    def upsert_transaction(self, txn_data: dict) -> None: ...
    def upsert_transactions_batch(self, txn_list: list[dict]) -> None: ...
    def get_transactions_in_range(self, start_date: str, end_date: str) -> list[tuple]: ...
    def get_last_sync_date(self) -> str | None: ...
    def set_last_sync_date(self, date_str: str) -> None: ...
    def get_server_knowledge(self, budget_id: str) -> int | None: ...   # delta-sync cursor (None => full fetch)
    def set_server_knowledge(self, budget_id: str, knowledge: int) -> None: ...  # advance only after a durable apply
    def delete_transaction(self, txn_id: str) -> None: ...              # applies YNAB `deleted: true`
    def save_summary(self, summary: dict) -> None: ...
    def get_summaries(self, limit: int = 12) -> list[tuple]: ...
```

### `TreasurerService(Service)`

```python
class TreasurerService(Service):
    def __init__(self, config_path: str): ...
    def run(self) -> None: ...                          # Main loop

    # Core operations
    def fetch_transactions_delta(self, budget_id: str, last_knowledge_of_server: int = None) -> tuple: ...  # (upserts, deleted_ids, server_knowledge)
    def sync_budget(self, ctx: BudgetContext) -> int: ...          # Delta sync; returns count of upserted txns
    def sync_all_budgets(self) -> None: ...
    def generate_summary(self, ctx: BudgetContext, start_date: str, end_date: str) -> dict: ...
    @staticmethod
    def _find_matched_duplicate_ids(rows: list) -> set: ...        # ids to drop so a YNAB matched pair counts once
    def trigger_monthly_summaries(self) -> None: ...

    # Budget lookup
    def find_budget_by_name(self, name: str) -> BudgetContext | None: ...   # case-insensitive: exact match, else single substring; ambiguous -> None
    def find_budget_by_id(self, budget_id: str) -> BudgetContext | None: ...
    def resolve_budget(self, budget_name: str = None, budget_id: str = None) -> BudgetContext | None: ...

    # Category resolution
    def resolve_category_name(self, ctx: BudgetContext, category_id: str) -> str: ...
```

### `TreasurerOracle(Oracle)`

```python
class TreasurerOracle(Oracle):
    def __init__(self, config: OracleConfig, service: TreasurerService): ...
    def init_endpoints(self) -> None: ...     # Registers HTTP routes
    def init_nla(self) -> None: ...           # Registers NLA handlers

    # Endpoint handlers
    def endpoint_get_summary(self) -> flask.Response: ...
    def endpoint_get_budgets(self) -> flask.Response: ...
    def endpoint_post_sync(self) -> flask.Response: ...
    def endpoint_get_summaries(self) -> flask.Response: ...

    # NLA handlers
    def nla_spending_summary(self, oracle: Oracle, params: dict) -> dict: ...
    def nla_list_budgets(self, oracle: Oracle, params: dict) -> dict: ...
```

## Interaction Flows

### Daily Sync Flow

```
┌─────────────┐    ┌──────────────┐    ┌──────────┐    ┌────────────────┐
│ Main Loop   │───▶│ sync_budget()│───▶│ YNAB API │───▶│ TransactionDB  │
│ (hour check)│    │              │    │          │    │ (upsert batch) │
└─────────────┘    └──────────────┘    └──────────┘    └────────────────┘
                          │
                          ▼
                   ┌────────────────────┐
                   │ Advance            │
                   │ server_knowledge   │
                   │ (only after apply) │
                   └────────────────────┘
```

### Monthly Report Flow

```
┌─────────────┐    ┌───────────────────┐    ┌────────────────┐    ┌──────────────┐
│ Main Loop   │───▶│ generate_summary()│───▶│ TransactionDB  │───▶│ Save summary │
│ (day check) │    │                   │    │ (date query)   │    │ to summaries │
└─────────────┘    └───────────────────┘    └────────────────┘    └──────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ ntfy_send()  │
                   │ (push notif) │
                   └──────────────┘
```

### On-Demand Summary Flow (Oracle)

```
┌──────────┐    ┌───────────────┐    ┌───────────────────┐    ┌────────────────┐
│ HTTP GET │───▶│ TreasurerOracle│───▶│ TreasurerService  │───▶│ TransactionDB  │
│ /summary │    │ (auth + parse)│    │ generate_summary()│    │ (query)        │
└──────────┘    └───────────────┘    └───────────────────┘    └────────────────┘
                                            │
                                            ▼
                                     ┌──────────────┐
                                     │ JSON Response│
                                     └──────────────┘
```

## Error Handling and Edge Cases

### YNAB API Errors

- **Rate limiting:** YNAB API has a 200 requests/hour limit. The daily sync should batch operations and use `since_date` to minimize calls. If rate-limited, log the error and retry on the next loop iteration.
- **Auth failures:** If the access token is invalid/expired, log the error, send an ntfy alert to the service's main `msghub_name`, and skip the sync cycle.
- **Network errors:** Wrap all YNAB API calls in try/except. On transient failures, log and retry next cycle. Do NOT update `last_sync_date` on failure.

### Database Errors

- **Table initialization:** Call `init_tables()` on startup and on every database access attempt (idempotent CREATE TABLE IF NOT EXISTS).
- **Concurrent access:** SQLite is single-writer. The service loop and Oracle endpoints both access the DB. Use the service's `self.lock` (inherited from `Service`) to serialize DB writes. Reads can proceed without locking (SQLite WAL mode recommended).
- **Corrupt database:** If a DB error occurs, log it and continue with other budgets.

### Edge Cases

- **First run (no last_sync_date):** When `last_sync_date` is None, perform a full sync (YNAB returns all transactions). This may be slow for large budgets but only happens once.
- **Budget removed from config:** Orphaned DB files are left in place (no destructive action). The service simply stops operating on that budget.
- **Duplicate transactions:** YNAB transaction IDs are stable. Using `INSERT OR REPLACE` with the transaction ID as primary key handles duplicates naturally.
- **Split transactions:** YNAB splits a transaction into sub-transactions with different categories. Each sub-transaction has its own ID and should be stored as an independent row.
- **Transfer transactions:** Transfers between accounts show up as two transactions (one positive, one negative). These should be stored as-is; the summary logic can optionally filter transfers if a category like "Transfer" is detected.
- **Category changes:** If a user re-categorizes a transaction in YNAB, the next sync (which uses `since_date`) will return the updated transaction, and `INSERT OR REPLACE` will overwrite the old row.
- **Month boundary for monthly trigger:** The 5th-of-month trigger uses an in-memory flag `(year, month)` to prevent duplicate triggers. If the service restarts on the 5th after already triggering, it will re-trigger. To prevent this, check the `summaries` table for an existing summary covering the previous month before generating a new one.

### Notification Formatting

Monthly summary notifications should be formatted as Markdown for readability:

```markdown
## 📊 Personal Budget — January 2025

**Income:** $5,200.00
**Expenses:** $3,245.67
**Net:** +$1,954.33

### Top Categories
| Category | Amount |
|----------|--------|
| Rent/Mortgage | $1,500.00 |
| Groceries | $845.23 |
| Restaurants | $312.44 |
| Utilities | $400.00 |
| Transportation | $188.00 |
```

## Future Extension Points

1. **Category budgets/targets:** Extend the summary to compare actual spending against YNAB category targets (available via the YNAB API's `get_categories` which includes `budgeted` amounts).

2. **Trend analysis:** With historical summaries stored, add an endpoint that compares month-over-month or year-over-year spending trends per category.

3. **Anomaly detection:** Flag unusual spending patterns (e.g., a category exceeding 2x its rolling average) and send proactive ntfy alerts.

4. **Custom report periods:** Support weekly or bi-weekly summaries in addition to monthly, configurable per budget.

5. **Account-level breakdown:** Add account filtering to the summary endpoint so users can analyze spending by account (e.g., credit card vs. checking).

6. **Inter-service integration:** Expose data to other DImROD services (e.g., Telegram bot commands like `/budget` for quick spending checks, or Speaker NLA for voice queries).

7. **Export endpoint:** Add an endpoint that exports transaction data or summaries to Excel (using the existing `export_to_excel()` from `lib/db.py`).

8. **Configurable sync frequency:** Allow more frequent syncing (e.g., every 6 hours) for users who want near-real-time data, while respecting YNAB rate limits.

9. **Transaction search endpoint:** Add a `/transactions` endpoint supporting filters (payee, category, amount range, date range) for ad-hoc queries.

10. **Multi-currency support:** If YNAB budgets use different currencies, store and display currency codes alongside amounts.

## Dependencies

* **Library modules:** `lib.service`, `lib.oracle`, `lib.config`, `lib.nla`, `lib.db`, `lib.ynab`, `lib.ntfy`, `lib.log`
* **Python standard library:** `datetime`, `time`, `json`, `hashlib`, `calendar`
* **External packages:** `ynab` (YNAB Python SDK), `flask`, `gevent`
* **External APIs:** YNAB API (via access token)
* **Other services:** None (Treasurer is queried by Speaker/Telegram, not the other way around)
