# This module defines the TransactionDatabaseConfig and TransactionDatabase
# classes used to persist and query financial transactions, summaries, and sync
# state for the Treasurer service. It wraps the Database class from lib/db.py
# to provide domain-specific operations for budget tracking.
#
#   Connor Shugg

# Imports
import os
import sys
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.db import DatabaseConfig, Database
from lib.ynab import YNABTransactionInfo

# Visible fields for Uniserdes SQLite serialization of transactions. The order
# here defines the column order in the transactions table (after the leading
# `encoded_obj` column). `deleted` and `parent_transaction_id` are appended last
# so that the ALTER TABLE migration in `init_tables` can add them to the end of
# pre-existing tables in the same order.
TRANSACTION_VISIBLE_FIELDS = [
    "id", "date", "amount", "category_name", "deleted", "parent_transaction_id"
]


class TransactionDatabaseConfig(DatabaseConfig):
    """Configuration for the transaction database. Extends DatabaseConfig from
    lib/db.py, inheriting the 'path' field. No additional fields are needed.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        # Inherits "path" field from DatabaseConfig.


class TransactionDatabase:
    """Wraps the Database class from lib/db.py to provide transaction-specific
    database operations for the Treasurer service. Manages three tables:
    transactions, summaries, and sync_state.
    """
    def __init__(self, config: TransactionDatabaseConfig):
        """Constructor. Initializes the database wrapper with the given config."""
        self.config = config
        self.db = Database(config)

        # Override get_connection to allow cross-thread usage.
        # This is safe because BudgetContext uses a threading.Lock for all
        # database operations.
        def _get_connection(reset=False):
            if self.db.conn is None or reset:
                if self.db.conn is not None:
                    self.db.conn.close()
                self.db.conn = sqlite3.connect(
                    self.db.config.path,
                    check_same_thread=False
                )
            return self.db.conn
        self.db.get_connection = _get_connection

        self.table_transactions = "transactions"
        self.table_summaries = "summaries"
        self.table_sync_state = "sync_state"
        self.init_tables()

    def init_tables(self) -> None:
        """Creates all tables and indexes if they don't exist."""
        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Use Uniserdes to generate the transactions table definition.
        # Create a template with placeholder values for required fields so
        # get_sqlite3_table_definition can inspect their types.
        txn_template = YNABTransactionInfo()
        txn_template.id = ""
        txn_template.date = ""
        txn_template.amount = 0.0
        create_stmt = txn_template.get_sqlite3_table_definition(
            "transactions",
            fields_to_keep_visible=TRANSACTION_VISIBLE_FIELDS,
            primary_key_field="id"
        )
        cursor.execute(create_stmt)

        # Migrate pre-existing transactions tables that were created before the
        # `deleted` / `parent_transaction_id` columns existed. `CREATE TABLE IF
        # NOT EXISTS` above is a no-op for those DBs, so new columns must be
        # added explicitly or positional INSERTs would fail. Adding a column is
        # idempotent here because we only add columns that are missing.
        self._migrate_transactions_columns(cursor)

        # Create indexes on the transactions table
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_date
            ON transactions(date);
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_transactions_category
            ON transactions(category_name);
        """)

        # Create the summaries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id TEXT PRIMARY KEY,
                budget_id TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_expenses REAL NOT NULL,
                total_income REAL NOT NULL,
                category_breakdown TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
        """)

        # Create the sync_state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        conn.commit()

    def _migrate_transactions_columns(self, cursor) -> None:
        """Adds any transactions columns introduced after the table was first
        created, so databases created by an older Treasurer version keep working.

        The base table is created via `CREATE TABLE IF NOT EXISTS`, which never
        adds columns to an existing table. When new visible fields are added to
        `YNABTransactionInfo` / `TRANSACTION_VISIBLE_FIELDS`, an existing DB will
        be missing those columns and positional `INSERT OR REPLACE ... VALUES`
        statements would fail. This method inspects the live schema and appends
        only the columns that are missing.

        Args:
            cursor: An open SQLite cursor on the transactions database.
        """
        # Columns that may be absent on older databases, with their SQLite type
        # and default. Defaults keep existing rows sensible: not deleted, and no
        # parent (i.e. a normal transaction).
        migrations = [
            ("deleted", "INTEGER DEFAULT 0"),
            ("parent_transaction_id", "TEXT"),
        ]

        existing = {
            row[1]
            for row in cursor.execute(
                "PRAGMA table_info(%s)" % self.table_transactions
            ).fetchall()
        }

        for column_name, column_def in migrations:
            if column_name in existing:
                continue
            cursor.execute(
                "ALTER TABLE %s ADD COLUMN %s %s" %
                (self.table_transactions, column_name, column_def)
            )

    def upsert_transaction(self, txn: YNABTransactionInfo) -> None:
        """Inserts or replaces a single transaction row."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        row = txn.to_sqlite3(fields_to_keep_visible=TRANSACTION_VISIBLE_FIELDS)
        placeholders = ", ".join(["?"] * len(row))
        cursor.execute(
            "INSERT OR REPLACE INTO transactions VALUES (%s)" % placeholders,
            row
        )
        conn.commit()

    def upsert_transactions_batch(self, txn_list: list) -> None:
        """Inserts or replaces multiple transactions in a single commit."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        for txn in txn_list:
            row = txn.to_sqlite3(fields_to_keep_visible=TRANSACTION_VISIBLE_FIELDS)
            placeholders = ", ".join(["?"] * len(row))
            cursor.execute(
                "INSERT OR REPLACE INTO transactions VALUES (%s)" % placeholders,
                row
            )
        conn.commit()

    def get_transactions_in_range(self, start_date: str, end_date: str) -> list:
        """Returns YNABTransactionInfo objects for transactions in the given date range.

        Dates are YYYY-MM-DD strings.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        results = cursor.execute(
            "SELECT * FROM transactions WHERE date >= ? AND date <= ?",
            (start_date, end_date)
        ).fetchall()

        transactions = []
        for row in results:
            txn = YNABTransactionInfo()
            txn.parse_sqlite3(row, fields_kept_visible=TRANSACTION_VISIBLE_FIELDS)
            transactions.append(txn)
        return transactions

    def get_last_sync_date(self) -> str:
        """Returns the value for key 'last_sync_date' from sync_state, or None."""
        results = self.db.search(
            self.table_sync_state,
            "key = ?",
            params=("last_sync_date",)
        )
        if isinstance(results, list):
            return None
        rows = results.fetchall()
        if len(rows) == 0:
            return None
        return rows[0][1]

    def set_last_sync_date(self, date_str: str) -> None:
        """Sets/updates the 'last_sync_date' key in sync_state."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?)",
            ("last_sync_date", date_str)
        )
        conn.commit()

    def get_server_knowledge(self, budget_id: str) -> int:
        """Returns the stored YNAB `server_knowledge` value for the given budget,
        or None if no delta sync has completed yet.

        The value is stored per budget in the sync_state table under the key
        "server_knowledge:<budget_id>" so a single database file can (in
        principle) track more than one budget. YNAB's delta endpoint accepts
        this value as `last_knowledge_of_server` and returns only entities that
        changed since it. A None result means "no knowledge yet" -> the caller
        should perform a full fetch (first sync / backfill).
        """
        key = "server_knowledge:%s" % budget_id
        results = self.db.search(
            self.table_sync_state,
            "key = ?",
            params=(key,)
        )
        if isinstance(results, list):
            return None
        rows = results.fetchall()
        if len(rows) == 0:
            return None
        try:
            return int(rows[0][1])
        except (TypeError, ValueError):
            return None

    def set_server_knowledge(self, budget_id: str, knowledge: int) -> None:
        """Sets/updates the `server_knowledge` value for the given budget in the
        sync_state table. The integer is stored as text (the sync_state.value
        column is TEXT NOT NULL). Callers must only advance this AFTER the
        corresponding transactions have been durably applied, so a failed run
        retries the same delta instead of skipping data.
        """
        key = "server_knowledge:%s" % budget_id
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?)",
            (key, str(knowledge))
        )
        conn.commit()

    def delete_transaction(self, txn_id: str) -> None:
        """Removes a transaction row by its YNAB transaction id (the primary
        key). Used to apply YNAB delta responses that mark a transaction
        `deleted: true`, so removed transactions drop out of summaries.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM transactions WHERE id = ?",
            (txn_id,)
        )
        conn.commit()

    def delete_transaction_and_children(self, txn_id: str) -> None:
        """Removes a transaction and all of its stored subtransaction children.

        Deletes the row whose primary key `id` equals `txn_id` AND every row
        whose `parent_transaction_id` equals `txn_id`. This is used when YNAB
        reports a transaction as `deleted: true` during delta sync: for a split
        transaction, YNAB frequently reports the deleted parent WITHOUT
        re-listing its subtransactions, so removing by parent id is required to
        avoid orphaning the previously-stored child rows (which are keyed by the
        subtransaction id and would otherwise linger in summaries forever).

        The operation is idempotent and retry-safe: deleting ids that are not
        present is a harmless no-op, so a failed sync can safely re-apply the
        same delta.

        Args:
            txn_id: The YNAB transaction id to remove (parent or standalone).
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM transactions WHERE id = ? OR parent_transaction_id = ?",
            (txn_id, txn_id)
        )
        conn.commit()

    def save_summary(self, summary: dict) -> None:
        """Inserts or replaces a summary row.

        summary keys: id, budget_id, start_date, end_date, total_expenses,
                      total_income, category_breakdown (JSON string),
                      generated_at
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary["id"],
                summary["budget_id"],
                summary["start_date"],
                summary["end_date"],
                summary["total_expenses"],
                summary["total_income"],
                summary["category_breakdown"],
                summary["generated_at"],
            )
        )
        conn.commit()

    def get_summaries(self, limit: int = 12) -> list:
        """Returns the most recent summaries, ordered by generated_at DESC."""
        results = self.db.search_order_by(
            self.table_summaries,
            order_by_column="generated_at",
            desc=True,
            limit=limit
        )
        if isinstance(results, list):
            return results
        return results.fetchall()

    def summary_exists(self, start_date: str, end_date: str) -> bool:
        """Returns True if a summary already exists for the given date range."""
        results = self.db.search(
            self.table_summaries,
            "start_date = ? AND end_date = ?",
            params=(start_date, end_date)
        )
        if isinstance(results, list):
            return False
        rows = results.fetchall()
        return len(rows) > 0
