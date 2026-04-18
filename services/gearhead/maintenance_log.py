# This module defines the MaintenanceLogEntry data model, the
# MaintenanceLogEntryStatus enum, the MaintenanceLogDatabaseConfig, the
# MaintenanceLogDatabase class, and metadata parsing utilities used by the
# Gearhead service's maintenance logging subsystem.
#
# The maintenance log provides persistent state for tracking when maintenance
# tasks are created (pending) and completed (done), enabling accurate
# deduplication and completion detection across polling cycles.

# Imports
import os
import sys
import re
import hashlib
import enum
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.db import DatabaseConfig, Database
from lib.uniserdes import Uniserdes, UniserdesField


# ========================= Metadata Footer Pattern ========================== #
# Regex pattern for parsing the machine-readable metadata footer embedded in
# Todoist task descriptions. Used by the TaskJob for completion detection.
#
# Footer format:
#   [dimrod::vehicle_maintenance vehicle={vehicle_id} task={task_id} trigger="{trigger_key}"]
#
# The trigger value is quoted to handle trigger_keys containing colons
# (e.g., "mileage:45000").
MAINTENANCE_METADATA_PATTERN = re.compile(
    r'\[dimrod::vehicle_maintenance\s+'
    r'vehicle=(\S+)\s+'
    r'task=(\S+)\s+'
    r'trigger="([^"]+)"\]'
)


def parse_maintenance_metadata(description):
    """Extracts vehicle_id, task_id, and trigger_key from a Todoist task
    description containing a machine-readable metadata footer.

    Args:
        description: The full Todoist task description string.

    Returns:
        A tuple of ``(vehicle_id, task_id, trigger_key)`` if a metadata
        footer is found, or ``None`` if no footer is present.
    """
    match = MAINTENANCE_METADATA_PATTERN.search(description)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


# ========================= Status Enum ====================================== #
class MaintenanceLogEntryStatus(enum.Enum):
    """Enum representing the status of a maintenance log entry.

    Only two statuses exist:
      - ``PENDING`` (0): A Todoist task has been created; user has not yet
        completed it.
      - ``DONE`` (1): The maintenance has been completed (detected via Todoist
        task completion or manually logged).

    Status transitions are append-only — a new entry is created rather than
    updating an existing one, providing a full audit trail.
    """
    PENDING = 0
    DONE = 1


# ========================= MaintenanceLogEntry ============================== #
class MaintenanceLogEntry(Uniserdes):
    """Represents a single maintenance log entry, recording either the creation
    of a maintenance reminder (PENDING) or the completion of a maintenance
    task (DONE).

    Each entry is uniquely identified by a SHA-256 hash of its key fields
    (vehicle_id, task_id, trigger_key, status, timestamp). The append-only
    design means a PENDING entry and a DONE entry for the same trigger produce
    different IDs.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("id",              [str],                        required=False, default=None),
            UniserdesField("vehicle_id",      [str],                        required=True),
            UniserdesField("task_id",         [str],                        required=True),
            UniserdesField("status",          [MaintenanceLogEntryStatus],  required=True),
            UniserdesField("trigger_key",     [str],                        required=False, default=None),
            UniserdesField("mileage",         [float],                      required=True),
            UniserdesField("timestamp",       [datetime],                   required=True),
            UniserdesField("todoist_task_id", [str],                        required=False, default=""),
            UniserdesField("notes",           [str],                        required=False, default=""),
        ]

    def get_id(self):
        """Generates and returns a unique ID for this maintenance log entry.

        The ID is a deterministic SHA-256 hash of the entry's key fields:
        vehicle_id, task_id, trigger_key, status value, and timestamp ISO
        string. Including status ensures that PENDING and DONE entries for
        the same trigger produce different IDs. Including timestamp
        guarantees uniqueness even for re-entries.

        Returns:
            str: The hex-encoded SHA-256 hash ID.
        """
        if self.id is None:
            trigger_key_str = self.trigger_key if self.trigger_key is not None else ""
            hash_str = "MaintenanceLogEntry|%s|%s|%s|%s|%s" % (
                self.vehicle_id,
                self.task_id,
                trigger_key_str,
                self.status.value,
                self.timestamp.isoformat()
            )
            self.id = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()
        return self.id

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        """Returns the list of field names that should be kept visible (not
        encoded) in the SQLite table.

        All queryable fields are kept visible for efficient SQL filtering.
        The ``notes`` field (potentially large, rarely queried) remains in
        the encoded blob.

        Returns:
            list[str]: Field names to keep as visible SQLite columns.
        """
        return [
            "id", "vehicle_id", "task_id", "status",
            "trigger_key", "mileage", "timestamp",
            "todoist_task_id"
        ]

    @staticmethod
    def make_mileage_trigger_key(threshold):
        """Generate a trigger_key for a mileage-based trigger.

        This is the single source of truth for mileage trigger key format.
        All code that generates mileage trigger keys must use this method.

        Args:
            threshold: The mileage threshold (int or float). Converted to
                int if it has no fractional part, otherwise to str.

        Returns:
            str: e.g., ``"mileage:45000"``
        """
        # Convert to int if the threshold is a whole number for clean keys
        if isinstance(threshold, float) and threshold == int(threshold):
            threshold = int(threshold)
        return "mileage:%s" % str(threshold)

    @staticmethod
    def make_datetime_trigger_key(date):
        """Generate a trigger_key for a datetime-based trigger.

        This is the single source of truth for datetime trigger key format.
        All code that generates datetime trigger keys must use this method.

        Args:
            date: A datetime object (or any object with ``.year`` and
                ``.month`` attributes).

        Returns:
            str: e.g., ``"datetime:2025-07"``
        """
        return "datetime:%s-%s" % (date.year, str(date.month).zfill(2))


# ========================= Database Config ================================== #
class MaintenanceLogDatabaseConfig(DatabaseConfig):
    """Configuration for the maintenance log database.

    Inherits the ``path`` field from ``DatabaseConfig``. No additional fields
    are needed — the single database file contains per-vehicle tables.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        # Inherits "path" from DatabaseConfig. No additional fields needed.


# ========================= Database Class =================================== #
class MaintenanceLogDatabase:
    """Wraps the Database class from lib/db.py to provide maintenance-log-specific
    database operations.

    Uses a single shared SQLite database file with one table per vehicle.
    Table names follow the convention ``log_{vehicle_id}``. All database
    access goes through the Database wrapper rather than raw sqlite3 calls.
    """
    def __init__(self, config: MaintenanceLogDatabaseConfig):
        """Constructor. Initializes the database wrapper with the given config.

        Args:
            config: A ``MaintenanceLogDatabaseConfig`` with the database path.
        """
        self.config = config
        self.db = Database(config)

    def _table_name(self, vehicle_id):
        """Returns the table name for a given vehicle.

        Args:
            vehicle_id: The sanitized vehicle ID (guaranteed whitespace-free).

        Returns:
            str: Table name in the format ``log_{vehicle_id}``.
        """
        return "log_%s" % vehicle_id

    def _ensure_table(self, vehicle_id, entry=None):
        """Ensures the table for the given vehicle exists in the database.

        Creates the table if it does not exist, using the
        ``MaintenanceLogEntry`` schema with visible fields and a primary key
        on ``id``.

        Args:
            vehicle_id: The vehicle ID whose table to ensure.
            entry: An optional ``MaintenanceLogEntry`` instance to use for
                schema generation. If ``None``, a new default instance is
                created.
        """
        table_name = self._table_name(vehicle_id)
        if not self.db.table_exists(table_name):
            if entry is None:
                entry = MaintenanceLogEntry()
                # Initialize defaults so the entry has all fields for schema
                # generation, including enum fields with proper types.
                entry.status = MaintenanceLogEntryStatus.PENDING
                entry.timestamp = datetime.now()
                entry.mileage = 0.0
                entry.id = ""
                entry.vehicle_id = ""
                entry.task_id = ""
                entry.trigger_key = None
                entry.todoist_task_id = ""
                entry.notes = ""
            fields_visible = MaintenanceLogEntry.sqlite3_fields_to_keep_visible()
            table_def = entry.get_sqlite3_table_definition(
                table_name,
                fields_to_keep_visible=fields_visible,
                primary_key_field="id"
            )
            self.db.execute(table_def, do_commit=False)

    def save(self, entry):
        """Inserts or replaces a maintenance log entry in the database.

        Ensures the vehicle's table exists before writing. Calls
        ``entry.get_id()`` to populate the ID if it hasn't been generated yet.

        Args:
            entry: A ``MaintenanceLogEntry`` to save.
        """
        entry.get_id()
        self._ensure_table(entry.vehicle_id, entry)
        fields_visible = MaintenanceLogEntry.sqlite3_fields_to_keep_visible()
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=fields_visible)
        table_name = self._table_name(entry.vehicle_id)
        self.db.insert_or_replace(table_name, str(sqlite3_obj))

    def _query(self, vehicle_id, condition, order_by="timestamp",
               desc=True, limit=None):
        """Internal helper that runs a search query on a vehicle's table.

        Returns an empty list if the table does not exist.

        Args:
            vehicle_id: The vehicle ID whose table to query.
            condition: SQL WHERE clause string.
            order_by: Column to order results by. Defaults to ``"timestamp"``.
            desc: Whether to sort descending. Defaults to ``True``.
            limit: Optional maximum number of results.

        Returns:
            list[MaintenanceLogEntry]: Parsed entries from the query results.
        """
        table_name = self._table_name(vehicle_id)
        if not self.db.table_exists(table_name):
            return []
        results = self.db.search(
            table_name,
            condition,
            order_by=order_by,
            desc=desc,
            limit=limit
        )
        entries = []
        for row in results:
            entries.append(MaintenanceLogEntry.from_sqlite3(row))
        return entries

    def search_by_task(self, vehicle_id, task_id, status=None, limit=None):
        """Returns all log entries for a specific maintenance task, optionally
        filtered by status.

        Args:
            vehicle_id: The vehicle ID to query.
            task_id: The maintenance task ID to filter by.
            status: Optional ``MaintenanceLogEntryStatus`` to filter by.
            limit: Optional maximum number of results.

        Returns:
            list[MaintenanceLogEntry]: Matching entries, ordered by timestamp
            descending.
        """
        condition = "task_id = \"%s\"" % task_id
        if status is not None:
            condition += " AND status = %d" % status.value
        return self._query(vehicle_id, condition, limit=limit)

    def search_by_trigger(self, vehicle_id, task_id, trigger_key):
        """Returns all log entries for a specific trigger occurrence.

        This is the core deduplication query. Returns all entries (pending
        and done) for a specific (vehicle, task, trigger_key) combination.

        If ``trigger_key`` is ``None``, returns entries where trigger_key
        is NULL or empty.

        Args:
            vehicle_id: The vehicle ID to query.
            task_id: The maintenance task ID.
            trigger_key: The trigger key to search for, or ``None``.

        Returns:
            list[MaintenanceLogEntry]: Matching entries, ordered by timestamp
            descending.
        """
        if trigger_key is None:
            condition = "task_id = \"%s\" AND (trigger_key IS NULL OR trigger_key = \"\")" % (
                task_id
            )
        else:
            condition = "task_id = \"%s\" AND trigger_key = \"%s\"" % (
                task_id, trigger_key
            )
        return self._query(vehicle_id, condition)

    def search_by_status(self, vehicle_id, status):
        """Returns all log entries for a vehicle filtered by status.

        Args:
            vehicle_id: The vehicle ID to query.
            status: A ``MaintenanceLogEntryStatus`` value to filter by.

        Returns:
            list[MaintenanceLogEntry]: Matching entries, ordered by timestamp
            descending.
        """
        condition = "status = %d" % status.value
        return self._query(vehicle_id, condition)

    def search_all_pending_with_todoist_id(self, vehicle_ids):
        """Returns all pending entries with a non-empty Todoist task ID across
        all provided vehicles.

        Iterates over each vehicle's table and aggregates results. Used by
        the TaskJob for completion detection.

        Args:
            vehicle_ids: List of vehicle ID strings to search across.

        Returns:
            list[MaintenanceLogEntry]: All pending entries with Todoist IDs,
            aggregated across all vehicles.
        """
        all_entries = []
        for vid in vehicle_ids:
            condition = "status = %d AND todoist_task_id != \"\"" % (
                MaintenanceLogEntryStatus.PENDING.value
            )
            entries = self._query(vid, condition)
            all_entries.extend(entries)
        return all_entries

    def search_by_mileage_range(self, vehicle_id, mileage_start, mileage_end):
        """Returns all log entries for a vehicle within a mileage range
        (inclusive on both ends).

        Args:
            vehicle_id: The vehicle ID to query.
            mileage_start: Minimum mileage (inclusive).
            mileage_end: Maximum mileage (inclusive).

        Returns:
            list[MaintenanceLogEntry]: Matching entries, ordered by timestamp
            descending.
        """
        condition = "mileage >= %s AND mileage <= %s" % (
            mileage_start, mileage_end
        )
        return self._query(vehicle_id, condition)

    def search_by_date_range(self, vehicle_id, time_start, time_end):
        """Returns all log entries for a vehicle within a date range
        (inclusive on both ends).

        Timestamps are compared as Unix epoch values to match the Uniserdes
        sqlite3 storage format.

        Args:
            vehicle_id: The vehicle ID to query.
            time_start: Start datetime (inclusive).
            time_end: End datetime (inclusive).

        Returns:
            list[MaintenanceLogEntry]: Matching entries, ordered by timestamp
            descending.
        """
        condition = "timestamp >= %s AND timestamp <= %s" % (
            time_start.timestamp(), time_end.timestamp()
        )
        return self._query(vehicle_id, condition)

    def search_latest_by_task(self, vehicle_id, task_id):
        """Returns the single most recent log entry for a given task,
        regardless of status.

        Args:
            vehicle_id: The vehicle ID to query.
            task_id: The maintenance task ID.

        Returns:
            MaintenanceLogEntry or None: The most recent entry, or ``None``
            if no entries exist for this task.
        """
        condition = "task_id = \"%s\"" % task_id
        results = self._query(vehicle_id, condition, limit=1)
        if len(results) == 0:
            return None
        return results[0]
