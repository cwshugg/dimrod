# This module defines the MileageEntry data model, the MileageDatabaseConfig,
# and the MileageDatabase class used to persist and query mileage readings for
# vehicles tracked by the Gearhead service.

# Imports
import os
import sys
import hashlib
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.db import DatabaseConfig, Database
from lib.uniserdes import Uniserdes, UniserdesField


class MileageEntry(Uniserdes):
    """Represents a single timestamped mileage reading for a vehicle. Uses a
    SHA-256 hash of the vehicle ID and timestamp to produce a stable, unique
    entry ID.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("id",           [str],      required=False, default=None),
            UniserdesField("vehicle_id",   [str],      required=True),
            UniserdesField("mileage",      [float],    required=True),
            UniserdesField("timestamp",    [datetime], required=True),
        ]

    def get_id(self):
        """Generates and returns a unique ID for this mileage entry based on
        the vehicle ID and timestamp. Uses SHA-256 hashing.
        """
        if self.id is None:
            hash_str = "MileageEntry|%s|%s" % (
                self.vehicle_id,
                self.timestamp.isoformat()
            )
            self.id = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()
        return self.id

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        """Returns the list of field names that should be kept visible (not
        encoded) in the SQLite table.
        """
        return ["id", "vehicle_id", "mileage", "timestamp"]


class MileageDatabaseConfig(DatabaseConfig):
    """Configuration for the mileage database. Extends DatabaseConfig from
    lib/db.py, inheriting the 'path' field. Additional mileage-db-specific
    fields can be appended here in the future.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        # Inherits "path" field from DatabaseConfig.


class MileageDatabase:
    """Wraps the Database class from lib/db.py to provide mileage-specific
    database operations. All database access goes through the Database wrapper
    rather than raw sqlite3 calls.
    """
    def __init__(self, config: MileageDatabaseConfig):
        """Constructor. Initializes the database wrapper with the given config."""
        self.config = config
        self.db = Database(config)
        self.table_name = "mileage"

    def _ensure_table(self, entry: MileageEntry = None):
        """Ensures the mileage table exists in the database. Creates it if it
        does not exist, using the MileageEntry schema.
        """
        if not self.db.table_exists(self.table_name):
            if entry is None:
                entry = MileageEntry()
            fields_visible = MileageEntry.sqlite3_fields_to_keep_visible()
            table_def = entry.get_sqlite3_table_definition(
                self.table_name,
                fields_to_keep_visible=fields_visible,
                primary_key_field="id"
            )
            self.db.execute(table_def, do_commit=False)

    def save(self, entry: MileageEntry):
        """Inserts or replaces a mileage entry in the database. Ensures the
        table exists before writing.
        """
        self._ensure_table(entry)
        fields_visible = MileageEntry.sqlite3_fields_to_keep_visible()
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=fields_visible)
        self.db.insert_or_replace(self.table_name, str(sqlite3_obj))

    def search_latest(self, vehicle_id: str):
        """Returns the most recent mileage entry for the given vehicle, or None
        if no entries exist.
        """
        if not self.db.table_exists(self.table_name):
            return None
        condition = "vehicle_id = \"%s\"" % vehicle_id
        results = self.db.search(
            self.table_name,
            condition,
            order_by="timestamp",
            desc=True,
            limit=1
        )
        rows = results.fetchall()
        if len(rows) == 0:
            return None
        return MileageEntry.from_sqlite3(rows[0])

    def search_history(self, vehicle_id: str, time_start=None, time_end=None):
        """Returns mileage entries for a vehicle, optionally filtered by a date
        range, ordered by timestamp descending. Timestamps are compared as Unix
        epoch values to match the Uniserdes sqlite3 storage format.
        """
        if not self.db.table_exists(self.table_name):
            return []
        condition = "vehicle_id = \"%s\"" % vehicle_id
        if time_start is not None:
            condition += " AND timestamp >= %s" % time_start.timestamp()
        if time_end is not None:
            condition += " AND timestamp <= %s" % time_end.timestamp()
        results = self.db.search(
            self.table_name,
            condition,
            order_by="timestamp",
            desc=True
        )
        entries = []
        for row in results:
            entries.append(MileageEntry.from_sqlite3(row))
        return entries
