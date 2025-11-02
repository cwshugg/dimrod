# This module implements code to work with Garmin connect data.

# Imports
import os
import sys
import enum
from datetime import datetime
import sqlite3
import hashlib

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu


# ========================== Database Entry Objects ========================== #
# Represents a single database entry for Garmin step data.
class GarminDatabaseStepsEntry(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=False, default=None),
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("step_count",       [int],      required=True),
            ConfigField("push_count",       [int],      required=False, default=0),
            ConfigField("activity_level",   [str],      required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, timezone=None):
        time_start = datetime.strptime(jdata["startGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        time_end = datetime.strptime(jdata["endGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        if timezone is not None:
            time_start = time_start.astimezone(tz=timezone)
            time_end = time_end.astimezone(tz=timezone)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "step_count": jdata["steps"],
            "push_count": jdata.get("pushes", 0),
            "activity_level": jdata.get("primaryActivityLevel", None)
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    # Turns the entry's start and end time into a unique ID string, such that
    # the exact same start/end times will produce the same ID string.
    def get_id(self):
        if self.id is None:
            datestr = "%s-%s" % (self.time_start.isoformat(),
                                 self.time_end.isoformat())
            data = datestr.encode("utf-8")
            self.id = hashlib.sha256(data).hexdigest()
        return self.id

    @staticmethod
    def sqlite3_fields_to_keep_visible():
        return ["id", "time_start", "time_end", "step_count"]


# ============================= Database Objects ============================= #
# A configuration object for a database.
class GarminDatabaseConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("db_path",                  [str],      required=True),
            ConfigField("reachback_seconds",        [int],      required=False, default=86400 * 365),
        ]

# An object used to interact with a Garmin step database.
class GarminDatabase:
    def __init__(self, config: GarminDatabaseConfig):
        self.config = config
        self.table_steps_name = "steps"

    # Performs a search of the database and returns tuples in a list.
    def search(self, table: str, condition: str):
        # build a SELECT command
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # connect, query, and return
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        result = cur.execute(cmd)
        return result

    # ------------------------------ Step Data ------------------------------- #
    # Inserts the provided entry into the database.
    def save_steps(self, entry: GarminDatabaseStepsEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseStepsEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_steps_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_steps_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_steps_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_steps_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple steps entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for step entries within the given time range.
    def search_steps_by_time_range(self, time_start: datetime, time_end: datetime):
        condition = "time_start >= %.f AND time_end <= %.f" % (
            time_start.timestamp(),
            time_end.timestamp()
        )
        result = self.search(self.table_steps_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

