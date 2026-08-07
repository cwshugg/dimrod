#!/usr/bin/python3
# This module implements the Treasurer service for syncing YNAB transactions,
# storing them locally, and producing spending analysis summaries. It provides
# HTTP endpoints and NLA (Natural Language Actions) for querying budgets and
# spending data within DImROD.
#
#   Connor Shugg

# Imports
import os
import sys
import json
import re
import time
import hashlib
import calendar
import threading
import flask
from datetime import datetime, date, timedelta

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.nla import NLAEndpoint, NLAEndpointInvokeParameters, NLAResult
from lib.cli import ServiceCLI
from lib.ynab import YNAB, YNABConfig, YNABTransactionInfo
from lib.ntfy import NtfyChannel
import lib.dtu as dtu

# Service imports
from db import TransactionDatabaseConfig, TransactionDatabase


# =============================== Config Classes ============================== #
class TreasurerExclusionConfig(Config):
    """Configuration for a single exclusion rule. Each rule specifies a
    combination of regex patterns for category, category group, and/or entity
    (payee). A transaction matches this exclusion if ALL specified fields match.
    Fields left unset (None) are treated as wildcards.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("category", [str], required=False, default=None),
            ConfigField("category_group", [str], required=False, default=None),
            ConfigField("entity", [str], required=False, default=None),
        ]

    def matches(self, category_name: str, group_name: str,
                payee_name: str) -> bool:
        """Returns True if the transaction fields match this exclusion rule.

        All non-None fields must match for the exclusion to apply (AND logic).
        """
        if self.category is not None:
            try:
                if not re.search(self.category, category_name or ""):
                    return False
            except re.error:
                return False
        if self.category_group is not None:
            try:
                if not re.search(self.category_group, group_name or ""):
                    return False
            except re.error:
                return False
        if self.entity is not None:
            try:
                if not re.search(self.entity, payee_name or ""):
                    return False
            except re.error:
                return False
        return True


class TreasurerMonthlySummaryConfig(Config):
    """Configuration for monthly spending summary behavior. Nested under each
    budget config to control what appears in the monthly report.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("top_categories", [int], required=False, default=10),
            ConfigField("top_income_transactions", [int], required=False,
                        default=5),
            ConfigField("expense_exclusions", [TreasurerExclusionConfig],
                        required=False, default=[]),
            ConfigField("income_exclusions", [TreasurerExclusionConfig],
                        required=False, default=[]),
        ]


class TreasurerBudgetConfig(Config):
    """Configuration for a single budget definition. Contains the YNAB budget
    UUID, a human-readable name, the path to the SQLite database file, and the
    ntfy.sh topic for notifications.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("budget_id",        [str], required=True),
            ConfigField("budget_name",      [str], required=True),
            ConfigField("db_path",          [str], required=True),
            ConfigField("ntfy_topic",       [str], required=True),
            ConfigField("monthly_summary",  [TreasurerMonthlySummaryConfig],
                        required=False, default=None),
        ]


class TreasurerConfig(ServiceConfig):
    """Main service configuration for Treasurer. Extends ServiceConfig with
    YNAB API credentials, budget definitions, and sync scheduling.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        fields = [
            ConfigField("ynab",      [YNABConfig],              required=True),
            ConfigField("budgets",   [TreasurerBudgetConfig],   required=True),
            ConfigField("sync_hour",    [int],                  required=False, default=3),
            ConfigField("summary_day",  [int],                  required=False, default=5),
            ConfigField("debug_summary", [bool],                required=False, default=False),
        ]
        self.fields += fields


# =============================== BudgetContext =============================== #
class BudgetContext:
    """Runtime container for a single budget's resources. Groups together the
    database, notification channel, and metadata for a configured budget.
    """
    def __init__(self, config: TreasurerBudgetConfig, config_dir: str):
        """Constructor. Initializes the database and ntfy channel for a budget.

        Args:
            config: The budget configuration object.
            config_dir: The directory containing the service config file,
                        used to resolve relative db_path values.
        """
        self.config = config
        self.budget_id = config.budget_id
        self.name = config.budget_name

        # Resolve db_path relative to config_dir (like gearhead does)
        db_path = config.db_path
        if not os.path.isabs(db_path):
            db_path = os.path.normpath(os.path.join(config_dir, db_path))

        # Create the database instance
        db_config = TransactionDatabaseConfig()
        db_config.parse_json({"path": db_path})
        self.db = TransactionDatabase(db_config)
        self.db.init_tables()

        # Create the notification channel
        self.ntfy = NtfyChannel(config.ntfy_topic)

        # Monthly summary config (use defaults if not provided)
        ms_config = config.monthly_summary
        if ms_config is None:
            ms_config = TreasurerMonthlySummaryConfig()
            ms_config.parse_json({})
        self.top_categories = ms_config.top_categories
        self.top_income_transactions = ms_config.top_income_transactions
        self.expense_exclusions = ms_config.expense_exclusions \
            if ms_config.expense_exclusions else []
        self.income_exclusions = ms_config.income_exclusions \
            if ms_config.income_exclusions else []

        # Category cache: category_id -> category_name
        self.category_cache = {}

        # Category group cache: category_id -> category_group_name
        self.category_group_cache = {}

        # Lock for thread-safe database access
        self.lock = threading.Lock()


# ================================= Service ================================== #
class TreasurerService(Service):
    """The main Treasurer service class. Manages YNAB transaction syncing,
    spending summary generation, and monthly report triggering for multiple
    budgets.
    """
    def __init__(self, config_path):
        """Constructor."""
        super().__init__(config_path)
        self.config = TreasurerConfig()
        self.config.parse_file(config_path)

        # Initialize the YNAB client
        self.ynab = YNAB(self.config.ynab)

        # Resolve config directory for relative paths
        config_dir = os.path.dirname(os.path.abspath(config_path))

        # Create BudgetContext for each configured budget
        self.budgets = []
        for budget_config in self.config.budgets:
            ctx = BudgetContext(budget_config, config_dir)
            self.budgets.append(ctx)
            self.log.write("Loaded budget: %s (%s)" % (ctx.name, ctx.budget_id))

        # Log available YNAB budgets for reference
        try:
            ynab_budgets = self.get_ynab_budgets()
            self.log.write("Available YNAB budgets:")
            for b in ynab_budgets:
                self.log.write("  - %s (%s)" % (b["name"], b["id"]))
        except Exception as e:
            self.log.write("Warning: could not fetch YNAB budgets on startup: %s" % str(e))

    def run(self):
        """Main loop: check for daily sync and monthly trigger."""
        super().run()
        last_sync_day = None
        last_trigger_month = None

        # TODO FIXME
        self.sync_all_budgets(force_full=True)

        while True:
            now = datetime.now()

            # Daily sync check
            if now.hour == self.config.sync_hour and last_sync_day != now.date():
                self.sync_all_budgets()
                last_sync_day = now.date()

            # Monthly trigger check (configurable day of month)
            #if now.day == self.config.summary_day and last_trigger_month != (now.year, now.month):
            # TODO FIXME
            if True:
                self.trigger_monthly_summaries()
                last_trigger_month = (now.year, now.month)

            time.sleep(60)

    def fetch_transactions_delta(self, budget_id: str,
                                 last_knowledge_of_server: int = None):
        """Fetch a YNAB transaction delta for a budget using server_knowledge.

        This bypasses the higher-level YNAB.get_transactions() wrapper because
        that wrapper (a) does not expose the response's `server_knowledge` and
        (b) silently drops transactions marked `deleted`. Delta sync needs both:
        the new server-knowledge to persist, and the deleted transactions so we
        can remove them locally.

        When `last_knowledge_of_server` is None, YNAB returns the FULL set of
        transactions (first sync / one-time full re-sync / backfill). When it is
        provided, YNAB returns only entities that were created, modified, or
        deleted since that knowledge value -- regardless of each transaction's
        DATE. This is what fixes the moving-date-cursor bug: a back-dated or
        late-clearing transaction still shows up in the delta because it changed
        on the server after the last sync.

        Args:
            budget_id: The YNAB budget id to fetch.
            last_knowledge_of_server: The previously-stored server_knowledge, or
                None to force a full fetch.

        Returns:
            A tuple (upserts, deleted_ids, server_knowledge) where:
              - upserts is a list of YNABTransactionInfo for created/modified
                transactions (splits expanded into their subtransactions),
              - deleted_ids is a list of YNAB transaction ids to remove locally,
              - server_knowledge is the new server-knowledge value to persist
                once the changes are durably applied.
        """
        api = self.ynab.api_transactions()
        r = api.get_transactions(
            budget_id,
            last_knowledge_of_server=last_knowledge_of_server
        )
        server_knowledge = r.data.server_knowledge

        upserts = []
        deleted_ids = []
        for t in r.data.transactions:
            # A deleted parent transaction removes itself and any of its
            # subtransactions from the local store.
            if getattr(t, "deleted", False):
                deleted_ids.append(t.id)
                for sub in (t.subtransactions or []):
                    deleted_ids.append(sub.id)
                continue

            subs = t.subtransactions or []
            if len(subs) > 0:
                # Split transaction: store each (non-deleted) subtransaction and
                # remove any subtransaction that was deleted in this delta.
                for sub in subs:
                    if getattr(sub, "deleted", False):
                        deleted_ids.append(sub.id)
                    else:
                        upserts.append(
                            YNABTransactionInfo.from_ynab_subtransaction(sub, t)
                        )
            else:
                upserts.append(YNABTransactionInfo.from_ynab_transaction(t))

        return upserts, deleted_ids, server_knowledge

    def sync_budget(self, ctx: BudgetContext, force_full: bool = False) -> int:
        """Sync transactions from YNAB for a single budget via delta sync.

        Uses YNAB's `server_knowledge` delta mechanism instead of a moving
        date cursor. The previously-stored server_knowledge is sent as
        `last_knowledge_of_server`; YNAB returns everything that changed since
        then (created/modified/deleted) regardless of transaction date, which
        the old date-window cursor could permanently skip. The new
        server_knowledge is persisted ONLY after the changes are durably applied
        so that a failed fetch or DB write retries the same delta next run
        rather than silently skipping data.

        On the first run after upgrade (no stored server_knowledge), the delta
        request has no knowledge value and YNAB returns the FULL transaction
        set, naturally backfilling any transactions the old cursor skipped.

        When `force_full` is True, the stored server_knowledge is ignored and a
        full fetch (`last_knowledge_of_server=None`) is performed. This is a
        safe, idempotent one-time backfill: it re-upserts EVERY transaction with
        the current field set (populating columns added after the rows were
        first synced, e.g. matched_transaction_id / import_id), then advances
        server_knowledge to the response value exactly like a normal sync. The
        stored server_knowledge is NOT cleared as the mechanism, so a crash
        mid-sync leaves the previous cursor intact and simply retries.

        Args:
            ctx: The BudgetContext to sync.
            force_full: When True, ignore the stored server_knowledge and fetch
                the full transaction set to backfill/refresh all local rows.

        Returns:
            The number of transactions upserted.
        """
        # Read the stored server_knowledge under lock. A forced full re-sync
        # ignores the stored value (None => full fetch) so YNAB returns the
        # entire transaction set and every local row is re-upserted with the
        # current field set. The stored value is left untouched until a durable
        # apply advances it, preserving retry-safety.
        with ctx.lock:
            stored_knowledge = ctx.db.get_server_knowledge(ctx.budget_id)
        last_knowledge = None if force_full else stored_knowledge

        # Fetch the delta from YNAB (network I/O — no lock needed). On ANY fetch
        # error, do NOT advance server_knowledge; return so the same delta is
        # retried next run instead of creating a permanent gap.
        try:
            upserts, deleted_ids, server_knowledge = \
                self.fetch_transactions_delta(
                    ctx.budget_id,
                    last_knowledge_of_server=last_knowledge
                )
        except Exception as e:
            self.log.write("Error fetching transactions for '%s': %s" %
                          (ctx.name, str(e)))
            return 0

        # Populate category cache if empty (write under lock for thread safety)
        if len(ctx.category_cache) == 0:
            try:
                categories = self.ynab.get_categories(ctx.budget_id)
                with ctx.lock:
                    for cat in categories:
                        ctx.category_cache[cat.id] = cat.name
                        ctx.category_group_cache[cat.id] = cat.category_group_name
            except Exception as e:
                self.log.write("Error fetching categories for '%s': %s" %
                              (ctx.name, str(e)))

        # Build YNABTransactionInfo objects with category names (in-memory — no
        # lock needed).
        txn_list = []
        for txn in upserts:
            category_name = self.resolve_category_name(ctx, txn.get_category_id())
            txn.category_name = category_name
            txn.category_group_name = ctx.category_group_cache.get(txn.category_id) if txn.category_id else None
            txn.synced_at = datetime.now().isoformat()
            txn_list.append(txn)

        # Apply DB changes and advance knowledge under lock. If applying the
        # changes fails, do NOT advance server_knowledge (and do not lose the
        # old value) so the same delta is retried next run.
        with ctx.lock:
            try:
                ctx.db.upsert_transactions_batch(txn_list)
                for txn_id in deleted_ids:
                    # Remove the transaction AND any stored subtransaction
                    # children. YNAB may report a deleted split parent without
                    # re-listing its subtransactions, so deleting by parent id
                    # is required to avoid orphaning the child rows.
                    ctx.db.delete_transaction_and_children(txn_id)
            except Exception as e:
                self.log.write("Error applying transactions for '%s': %s" %
                              (ctx.name, str(e)))
                return 0

            # Only now, after a successful apply, advance the persisted
            # server_knowledge so the next sync continues from this point.
            if server_knowledge is not None:
                ctx.db.set_server_knowledge(ctx.budget_id, server_knowledge)
            # Keep last_sync_date updated for observability (delta sync no longer
            # depends on it, but it is retained alongside server_knowledge).
            ctx.db.set_last_sync_date(dtu.format_yyyymmdd(datetime.now()))

        return len(txn_list)

    def sync_all_budgets(self, force_full: bool = False):
        """Calls sync_budget() for each budget context, logging results.

        Args:
            force_full: When True, each budget is synced with a full re-fetch
                (see sync_budget) rather than an incremental delta.
        """
        self.log.write("Starting %s sync for all budgets..." %
                       ("full re-" if force_full else "daily"))
        for ctx in self.budgets:
            try:
                count = self.sync_budget(ctx, force_full=force_full)
                self.log.write("Synced %d transactions for '%s'." %
                              (count, ctx.name))
            except Exception as e:
                self.log.write("Error syncing budget '%s': %s" %
                              (ctx.name, str(e)))

    @staticmethod
    def _find_matched_duplicate_ids(rows: list) -> set:
        """Identify YNAB matched-transaction duplicates to drop from a summary.

        When a manually-entered transaction and a later bank-imported
        transaction are MATCHED by YNAB, the delta returns BOTH objects
        cross-referencing each other via `matched_transaction_id`. Treasurer
        stores both, so counting both double-counts the single real charge.
        This returns the set of row ids to DROP so each matched pair collapses
        to exactly one counted transaction.

        Selection rules (deterministic and idempotent):
          * Only a row whose `matched_transaction_id` is non-null AND whose
            partner id is also present forms a collapsible pair. If only one
            half is present (e.g. the other was deleted), nothing is dropped and
            the present half is kept unchanged.
          * The KEPT half is chosen by the following priority:
              1. CATEGORIZED over uncategorized. A row is "categorized" when its
                 `category_name` is truthy AND not equal (case-insensitive,
                 stripped) to "Uncategorized" AND its `category_group_name` is
                 not None/empty. If exactly one half is categorized, that half
                 is kept (the imported half is frequently "Uncategorized" and
                 would distort the category breakdown and exclusion matching).
              2. If both halves tie on rule 1 (both categorized or both
                 uncategorized), prefer the MANUAL half (`import_id` is None):
                 keep manual, drop imported.
              3. If still tied (same import status), the row with the
                 lexicographically greater id is dropped as a stable tie-break.

        De-dup is based ONLY on YNAB's explicit match linkage -- never on equal
        date/amount/payee -- so intentionally duplicated but UNLINKED
        transactions (two identical real payments) are both preserved.

        Args:
            rows: The `YNABTransactionInfo` rows under consideration.

        Returns:
            A set of row ids to drop from the summary.
        """
        rows_by_id = {row.id: row for row in rows}
        dropped = set()

        def _is_categorized(r) -> bool:
            """A row is categorized when it has a real (non-Uncategorized)
            category name AND a non-empty category group name."""
            name = getattr(r, "category_name", None)
            if not name or name.strip().lower() == "uncategorized":
                return False
            group = getattr(r, "category_group_name", None)
            return bool(group)

        for row in rows:
            mid = getattr(row, "matched_transaction_id", None)
            if mid is None:
                continue
            partner = rows_by_id.get(mid)
            if partner is None:
                # Only one half of the pair is present -> keep it, drop nothing.
                continue
            # Both halves present: choose which to KEEP by priority.
            # Rule 1: categorized over uncategorized.
            row_cat = _is_categorized(row)
            partner_cat = _is_categorized(partner)
            if row_cat and not partner_cat:
                dropped.add(partner.id)
                continue
            if partner_cat and not row_cat:
                dropped.add(row.id)
                continue
            # Rule 2: tie on categorization -> prefer the manual half.
            row_manual = getattr(row, "import_id", None) is None
            partner_manual = getattr(partner, "import_id", None) is None
            if row_manual and not partner_manual:
                dropped.add(partner.id)
            elif partner_manual and not row_manual:
                dropped.add(row.id)
            else:
                # Rule 3: same import status -> drop the greater id (stable).
                dropped.add(max(row.id, partner.id))
        return dropped

    def generate_summary(self, ctx: BudgetContext, start_date: datetime,
                         end_date: datetime) -> dict:
        """Generate a spending summary from the local DB.

        Transactions matching the budget's expense or income exclusion rules
        are omitted from calculations. Each exclusion rule can specify regex
        patterns for category, category_group, and/or entity (payee).

        Args:
            ctx: The BudgetContext to generate a summary for.
            start_date: Start of range (inclusive) as a datetime object.
            end_date: End of range (inclusive) as a datetime object.

        Returns:
            A dict containing budget_name, start_date, end_date,
            total_expenses, total_income, net, categories, and
            transaction_count.
        """
        # Convert datetime objects to strings for DB query
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Acquire lock only for the DB read
        with ctx.lock:
            rows = ctx.db.get_transactions_in_range(start_str, end_str)

        # Computation does not need the lock
        total_expenses = 0.0
        total_income = 0.0
        category_breakdown = {}
        income_transactions = []
        transaction_count = 0

        # Identify YNAB matched-transaction duplicates so each matched pair is
        # counted only once. This is linkage-based (see
        # `_find_matched_duplicate_ids`), so intentionally duplicated but
        # unlinked transactions are unaffected.
        matched_duplicate_ids = self._find_matched_duplicate_ids(rows)

        if self.config.debug_summary:
            self.log.write("Generating summary for \"%s\" (%s to %s)..." %
                          (ctx.name, start_str, end_str))

        for row in rows:
            category_name = row.category_name or "(Uncategorized)"
            group_name = row.category_group_name
            payee_name = row.payee_name
            amount = row.amount

            # Build a debug string to print for each transaction
            transaction_debug_str = "date=\"%s\", amount=%.2f, entity=\"%s\", category=\"%s\", category_group=\"%s\", description=\"%s\"" % \
                                    (row.date, amount, payee_name, category_name, group_name, row.memo)

            # Defensively skip any row still flagged `deleted` that lingers in
            # the DB. Deleted rows are normally removed at sync time, but this
            # belt-and-suspenders guard ensures they never reach a summary.
            if getattr(row, "deleted", 0):
                transaction_debug_str = "(SKIP; Deleted) %s" % transaction_debug_str
                if self.config.debug_summary:
                    self.log.write(transaction_debug_str)
                continue

            # Collapse YNAB matched-transaction pairs: skip the dropped half so
            # the single real charge is only counted once. The kept half is the
            # categorized (else manual) transaction (see
            # `_find_matched_duplicate_ids`).
            if row.id in matched_duplicate_ids:
                transaction_debug_str = "(SKIP; Matched duplicate) %s" % transaction_debug_str
                if self.config.debug_summary:
                    self.log.write(transaction_debug_str)
                continue

            if row.is_transfer():
                transaction_debug_str = "(SKIP; Transfer) %s" % transaction_debug_str
                if self.config.debug_summary:
                    self.log.write(transaction_debug_str)
                continue

            # Apply the appropriate exclusion list based on direction
            if amount < 0:
                if ctx.expense_exclusions:
                    if any(exc.matches(category_name, group_name, payee_name)
                           for exc in ctx.expense_exclusions):
                        # Log a debug string and skip
                        transaction_debug_str = "(SKIP; Expense exclusion) %s" % transaction_debug_str
                        if self.config.debug_summary:
                            self.log.write(transaction_debug_str)
                        continue
            elif amount == 0:
                # Log a debug string and skip
                transaction_debug_str = "(SKIP; Zero-cost transaction) %s" % transaction_debug_str
                if self.config.debug_summary:
                    self.log.write(transaction_debug_str)
                continue
            else:
                if ctx.income_exclusions:
                    if any(exc.matches(category_name, group_name, payee_name)
                           for exc in ctx.income_exclusions):
                        # Log a debug string and skip
                        transaction_debug_str = "(SKIP; Income exclusion) %s" % transaction_debug_str
                        if self.config.debug_summary:
                            self.log.write(transaction_debug_str)
                        continue

            # Log the transaction, if debug is enabled:
            if self.config.debug_summary:
                self.log.write(transaction_debug_str)

            transaction_count += 1

            if amount < 0:
                total_expenses += abs(amount)
                if category_name not in category_breakdown:
                    category_breakdown[category_name] = {
                        "amount": 0.0,
                        "group_name": group_name
                    }
                category_breakdown[category_name]["amount"] += amount
            else:
                total_income += amount
                income_transactions.append({
                    "payee_name": row.payee_name or "Unknown",
                    "amount": amount,
                    "date": row.date,
                    "category": category_name,
                    "memo": row.memo,
                })

        # Sort income transactions by amount descending
        income_transactions.sort(key=lambda x: x["amount"], reverse=True)
        net = total_income - total_expenses

        return {
            "budget_name": ctx.name,
            "start_date": start_str,
            "end_date": end_str,
            "total_expenses": total_expenses,
            "total_income": total_income,
            "net": net,
            "categories": category_breakdown,
            "income_transactions": income_transactions,
            "transaction_count": transaction_count,
        }

    def trigger_monthly_summaries(self):
        """For each budget, generate and send the previous month's summary.

        Calculates the previous month's date range, checks if a summary
        already exists, generates the summary, saves it to the database,
        and sends it via ntfy.
        """
        self.log.write("Triggering monthly summaries...")
        now = datetime.now()

        # Calculate previous month range
        if now.month == 1:
            prev_year = now.year - 1
            prev_month = 12
        else:
            prev_year = now.year
            prev_month = now.month - 1

        first_day = date(prev_year, prev_month, 1)
        last_day_num = calendar.monthrange(prev_year, prev_month)[1]
        last_day = date(prev_year, prev_month, last_day_num)

        start_date = first_day.strftime("%Y-%m-%d")
        end_date = last_day.strftime("%Y-%m-%d")

        # Convert to datetime objects for generate_summary()
        start_dt = datetime(first_day.year, first_day.month, first_day.day)
        end_dt = datetime(last_day.year, last_day.month, last_day.day)

        for ctx in self.budgets:
            try:
                # Check if summary already exists (under lock to prevent races)
                with ctx.lock:
                    if ctx.db.summary_exists(start_date, end_date):
                        self.log.write("Summary already exists for '%s' (%s to %s), skipping." %
                                      (ctx.name, start_date, end_date))
                        continue

                # Generate summary (acquires its own lock internally for DB read)
                summary = self.generate_summary(ctx, start_dt, end_dt)

                # Save to summaries table (under lock for thread safety)
                summary_id = hashlib.sha256(
                    (ctx.budget_id + start_date + end_date).encode()
                ).hexdigest()

                summary_record = {
                    "id": summary_id,
                    "budget_id": ctx.budget_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "total_expenses": summary["total_expenses"],
                    "total_income": summary["total_income"],
                    "category_breakdown": json.dumps(summary["categories"]),
                    "generated_at": datetime.now().isoformat(),
                }
                with ctx.lock:
                    ctx.db.save_summary(summary_record)

                # Format and send via ntfy
                month_name = first_day.strftime("%B")
                message = self._format_monthly_summary(
                    ctx.name, month_name, prev_year, summary, top_n=ctx.top_categories,
                    top_income_n=ctx.top_income_transactions,
                    expense_exclusions=ctx.expense_exclusions
                )
                ctx.ntfy.post(message,
                             title="📊 %s — %s %d" % (ctx.name, month_name, prev_year))

                self.log.write("Monthly summary sent for '%s' (%s %d)." %
                              (ctx.name, month_name, prev_year))
            except Exception as e:
                self.log.write("Error generating monthly summary for '%s': %s" %
                              (ctx.name, str(e)))

    def _format_monthly_summary(self, budget_name: str, month_name: str,
                                year: int, summary: dict, top_n: int = 10,
                                top_income_n: int = 5,
                                expense_exclusions: list = None) -> str:
        """Formats a monthly summary as a bulleted message for ntfy.

        Args:
            budget_name: Human-readable budget name.
            month_name: Name of the month (e.g., "January").
            year: The year.
            summary: The summary dict from generate_summary().
            top_n: Number of top expense categories to display.
            top_income_n: Number of top income transactions to display.
            expense_exclusions: List of TreasurerExclusionConfig objects for
                defense-in-depth filtering of the expense category display.

        Returns:
            A formatted string.
        """
        net = summary["net"]
        net_sign = "+" if net >= 0 else "-"
        net_abs = abs(net)

        msg = "%s — %s %d\n\n" % (budget_name, month_name, year)
        msg += "• Income: $%s\n" % format(summary["total_income"], ",.2f")
        msg += "• Expenses: $%s\n" % format(summary["total_expenses"], ",.2f")
        msg += "• Net: %s$%s\n\n" % (net_sign, format(net_abs, ",.2f"))

        # Top categories (sorted by absolute amount, descending)
        categories = summary.get("categories", {})
        if categories:
            # Build list of (name, amount, group_name) tuples
            cat_list = [(name, data["amount"], data.get("group_name"))
                        for name, data in categories.items()]
            # Sort by absolute amount descending
            cat_list.sort(key=lambda x: abs(x[1]), reverse=True)

            # Defense-in-depth: filter by expense exclusion rules
            # None is passed for payee_name because categories don't have a
            # single associated payee; entity-based rules won't fire here.
            if expense_exclusions:
                cat_list = [(n, a, g) for n, a, g in cat_list
                            if not any(exc.matches(n, g, None)
                                       for exc in expense_exclusions)]

            msg += "Top %d Expense Categories:\n\n" % top_n
            for name, amount, _ in cat_list[:top_n]:
                msg += "• %s: $%s\n" % (name, format(abs(amount), ",.2f"))

        # Top income transactions
        income_txns = summary.get("income_transactions", [])
        if income_txns and top_income_n > 0:
            msg += "\nTop %d Income Transactions:\n\n" % top_income_n
            for txn in income_txns[:top_income_n]:
                memo = txn.get("memo")
                if memo:
                    msg += "• %s: $%s (%s) — %s\n" % (
                        txn["payee_name"],
                        format(txn["amount"], ",.2f"),
                        txn["date"],
                        memo
                    )
                else:
                    msg += "• %s: $%s (%s)\n" % (
                        txn["payee_name"],
                        format(txn["amount"], ",.2f"),
                        txn["date"]
                    )

        return msg

    def get_ynab_budgets(self) -> list:
        """Queries the YNAB API for all available budgets.

        Returns:
            A list of dicts, each with 'id' and 'name' keys.
        """
        budgets = self.ynab.get_budgets()
        result = []
        for b in budgets:
            result.append({
                "id": b.id,
                "name": b.name,
            })
        return result

    def find_budget_by_name(self, name: str) -> BudgetContext:
        """Finds a budget context by name. Tries an exact (case-insensitive)
        match first, then falls back to a case-insensitive substring match if
        exactly one budget name contains the search term.

        Matching is case-insensitive (via `casefold`) so any casing of the
        budget name resolves the budget -- e.g. the telegram command
        `/b summary master` resolves "Master Budget". The incoming name is
        stripped of surrounding whitespace before comparison.

        Args:
            name: The budget name (or partial name) to search for.

        Returns:
            The matching BudgetContext, or None if not found or
            ambiguous.
        """
        # Normalize the incoming name: strip surrounding whitespace and
        # casefold for case-insensitive comparison. A None/blank name matches
        # nothing.
        if name is None:
            return None
        normalized = name.strip().casefold()
        if len(normalized) == 0:
            return None

        # Try an exact (case-insensitive) match first.
        for ctx in self.budgets:
            if ctx.name.casefold() == normalized:
                return ctx

        # Fall back to a case-insensitive substring match, returning a result
        # ONLY when exactly one budget contains the term (ambiguity guard: if
        # 2+ budgets contain it, the intent is undefined -> return None).
        partial_matches = [
            ctx for ctx in self.budgets
            if normalized in ctx.name.casefold()
        ]
        if len(partial_matches) == 1:
            return partial_matches[0]

        return None

    def find_budget_by_id(self, budget_id: str) -> BudgetContext:
        """Finds a budget context by its YNAB budget UUID.

        Args:
            budget_id: The YNAB budget UUID.

        Returns:
            The matching BudgetContext, or None if not found.
        """
        for ctx in self.budgets:
            if ctx.budget_id == budget_id:
                return ctx
        return None

    def resolve_budget(self, budget_name=None, budget_id=None) -> BudgetContext:
        """Resolves a budget context from a name or ID. If both are provided,
        budget_id takes precedence.

        Args:
            budget_name: Optional budget name.
            budget_id: Optional budget UUID.

        Returns:
            The matching BudgetContext, or None if not found.
        """
        if budget_id is not None:
            return self.find_budget_by_id(budget_id)
        if budget_name is not None:
            return self.find_budget_by_name(budget_name)
        return None

    def resolve_category_name(self, ctx: BudgetContext,
                              category_id: str) -> str:
        """Resolves a category ID to its human-readable name using the cache.

        Args:
            ctx: The BudgetContext whose cache to use.
            category_id: The YNAB category UUID.

        Returns:
            The category name, or "Uncategorized" if not found.
        """
        if category_id is None:
            return "Uncategorized"
        if category_id in ctx.category_cache:
            return ctx.category_cache[category_id]
        return "Uncategorized"


# ============================== Service Oracle ============================== #
class TreasurerOracle(Oracle):
    """Oracle for the Treasurer service. Defines HTTP endpoints and NLA
    registration for budget and spending operations.
    """
    def endpoints(self):
        """Endpoint definition function."""
        super().endpoints()

        # GET /summary — returns a spending summary for a budget over a date range
        @self.server.route("/summary", methods=["GET"])
        def endpoint_summary():
            """Returns a spending summary for a budget over a date range."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            jdata = flask.g.jdata
            budget_name = jdata.get("budget_name", None)
            budget_id = jdata.get("budget_id", None)

            if budget_name is None and budget_id is None:
                return self.make_response(
                    msg="Must specify 'budget_name' or 'budget_id'.",
                    success=False, rstatus=400)

            if "start_date" not in jdata or "end_date" not in jdata:
                return self.make_response(
                    msg="Must specify 'start_date' and 'end_date'.",
                    success=False, rstatus=400)

            start_date = str(jdata["start_date"])
            end_date = str(jdata["end_date"])

            ctx = self.service.resolve_budget(
                budget_name=budget_name, budget_id=budget_id
            )
            if ctx is None:
                return self.make_response(
                    msg="Budget not found.",
                    success=False, rstatus=404)

            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                summary = self.service.generate_summary(ctx, start_dt, end_dt)
                return self.make_response(success=True,
                                          msg="Summary generated.",
                                          payload=summary)
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

        # GET /budgets — returns list of configured budgets
        @self.server.route("/budgets", methods=["GET"])
        def endpoint_budgets():
            """Returns a list of all configured budgets."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            budgets = []
            for ctx in self.service.budgets:
                budgets.append({
                    "budget_id": ctx.budget_id,
                    "name": ctx.name,
                })
            return self.make_response(success=True,
                                      payload={"budgets": budgets})

        # GET /ynab/budgets — queries YNAB API for all available budgets
        @self.server.route("/ynab/budgets", methods=["GET"])
        def endpoint_ynab_budgets():
            """Queries YNAB for all available budgets and returns their IDs and names."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            try:
                budgets = self.service.get_ynab_budgets()
                return self.make_response(success=True, payload={"budgets": budgets})
            except Exception as e:
                return self.make_response(msg="Error querying YNAB: %s" % str(e),
                                           success=False, rstatus=500)

        # POST /sync — triggers sync for one or all budgets
        @self.server.route("/sync", methods=["POST"])
        def endpoint_sync():
            """Manually triggers a transaction sync."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            jdata = flask.g.jdata or {}
            budget_name = jdata.get("budget_name", None)
            budget_id = jdata.get("budget_id", None)
            # Optional one-time full re-sync flag. Accept either "full" or
            # "force_full" for convenience; both default to False (delta sync).
            force_full = bool(jdata.get("full", jdata.get("force_full", False)))
            full_note = " (full re-sync)" if force_full else ""

            if budget_name is not None or budget_id is not None:
                # Sync a specific budget
                ctx = self.service.resolve_budget(
                    budget_name=budget_name, budget_id=budget_id
                )
                if ctx is None:
                    return self.make_response(
                        msg="Budget not found.",
                        success=False, rstatus=404)
                try:
                    count = self.service.sync_budget(
                        ctx, force_full=force_full)
                    return self.make_response(
                        success=True,
                        msg="Synced %d transactions for '%s'%s." %
                            (count, ctx.name, full_note))
                except Exception as e:
                    return self.make_response(msg=str(e),
                                              success=False, rstatus=400)
            else:
                # Sync all budgets
                try:
                    self.service.sync_all_budgets(force_full=force_full)
                    return self.make_response(
                        success=True,
                        msg="Synced all budgets%s." % full_note)
                except Exception as e:
                    return self.make_response(msg=str(e),
                                              success=False, rstatus=400)

        # GET /summaries — returns historical summaries for a budget
        @self.server.route("/summaries", methods=["GET"])
        def endpoint_summaries():
            """Returns stored historical summaries for a budget."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            jdata = flask.g.jdata
            budget_name = jdata.get("budget_name", None)
            budget_id = jdata.get("budget_id", None)

            if budget_name is None and budget_id is None:
                return self.make_response(
                    msg="Must specify 'budget_name' or 'budget_id'.",
                    success=False, rstatus=400)

            ctx = self.service.resolve_budget(
                budget_name=budget_name, budget_id=budget_id
            )
            if ctx is None:
                return self.make_response(
                    msg="Budget not found.",
                    success=False, rstatus=404)

            limit = jdata.get("limit", 12)
            if type(limit) not in [int]:
                return self.make_response(
                    msg="'limit' must be an integer.",
                    success=False, rstatus=400)

            with ctx.lock:
                rows = ctx.db.get_summaries(limit=limit)
            summaries = []
            for row in rows:
                # Row: id, budget_id, start_date, end_date, total_expenses,
                #      total_income, category_breakdown, generated_at
                summaries.append({
                    "start_date": row[2],
                    "end_date": row[3],
                    "total_expenses": row[4],
                    "total_income": row[5],
                    "category_breakdown": json.loads(row[6]),
                    "generated_at": row[7],
                })

            return self.make_response(success=True,
                                      payload={"summaries": summaries})

    def init_nla(self):
        """Registers NLA endpoints for budget and spending operations."""
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                "name": "spending_summary",
                "description": "Get a spending summary for a budget over a date range. "
                               "Specify the budget name and date range."
            }).set_handler(nla_spending_summary),
            NLAEndpoint.from_json({
                "name": "list_budgets",
                "description": "List all configured budgets that can be queried "
                               "for spending data."
            }).set_handler(nla_list_budgets),
        ]


# =============================== NLA Handlers =============================== #
def nla_spending_summary(oracle, jdata):
    """NLA handler that generates a spending summary for a budget identified
    from the user's natural language message.
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    user_text = params.message
    if params.has_substring():
        user_text = params.substring

    # Try to match budget by substring (sort by name length descending so
    # longer/more-specific names are matched first)
    matched_ctx = None
    text_lower = user_text.lower()
    sorted_budgets = sorted(oracle.service.budgets, key=lambda c: len(c.name), reverse=True)
    for ctx in sorted_budgets:
        if ctx.name.lower() in text_lower:
            matched_ctx = ctx
            break

    if matched_ctx is None:
        names = [ctx.name for ctx in oracle.service.budgets]
        return NLAResult.from_json({
            "success": False,
            "message": "I could not determine which budget you mean. "
                       "Available budgets: %s" % ", ".join(names)
        })

    # Extract date range from extra_params
    start_date = None
    end_date = None
    if params.extra_params:
        start_date = params.extra_params.get("start_date", None)
        end_date = params.extra_params.get("end_date", None)

    # Default to previous month if no dates specified
    if start_date is None or end_date is None:
        now = datetime.now()
        if now.month == 1:
            prev_year = now.year - 1
            prev_month = 12
        else:
            prev_year = now.year
            prev_month = now.month - 1
        first_day = date(prev_year, prev_month, 1)
        last_day_num = calendar.monthrange(prev_year, prev_month)[1]
        last_day = date(prev_year, prev_month, last_day_num)
        start_dt = datetime(first_day.year, first_day.month, first_day.day)
        end_dt = datetime(last_day.year, last_day.month, last_day.day)
    else:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    try:
        summary = oracle.service.generate_summary(
            matched_ctx, start_dt, end_dt
        )
    except Exception as e:
        return NLAResult.from_json({
            "success": False,
            "message": "Error generating summary: %s" % str(e)
        })

    # Format top categories for the message
    categories = summary.get("categories", {})
    sorted_cats = sorted(categories.items(), key=lambda x: abs(x[1]),
                        reverse=True)[:3]
    top_cats_str = ", ".join(
        "%s ($%s)" % (name, format(abs(amount), ",.2f"))
        for name, amount in sorted_cats
    )

    msg = ("%s spending from %s to %s: $%s out, $%s in." %
           (summary["budget_name"], start_date, end_date,
            format(summary["total_expenses"], ",.2f"),
            format(summary["total_income"], ",.2f")))
    if top_cats_str:
        msg += " Top categories: %s." % top_cats_str

    return NLAResult.from_json({
        "success": True,
        "message": msg,
        "message_context": "spending summary",
        "payload": {
            "total_expenses": summary["total_expenses"],
            "total_income": summary["total_income"],
            "categories": summary["categories"],
        }
    })


def nla_list_budgets(oracle, jdata):
    """NLA handler that returns the names of all configured budgets."""
    params = NLAEndpointInvokeParameters.from_json(jdata)

    budget_strs = []
    for ctx in oracle.service.budgets:
        budget_strs.append("· %s" % ctx.name)

    msg = "Configured budgets:\n" + "\n".join(budget_strs)

    return NLAResult.from_json({
        "success": True,
        "message": msg,
        "message_context": "budget list",
        "payload": {
            "budgets": [{"name": ctx.name, "budget_id": ctx.budget_id}
                       for ctx in oracle.service.budgets]
        }
    })


# ================================== Main ==================================== #
if __name__ == "__main__":
    cli = ServiceCLI(config=TreasurerConfig,
                     service=TreasurerService,
                     oracle=TreasurerOracle)
    cli.run()
