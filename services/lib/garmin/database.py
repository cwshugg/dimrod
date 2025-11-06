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


# ====================== Generic Database Entry Objects ====================== #
class GarminDatabaseEntryBase(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=False, default=None),
        ]

    # Turns the entry's start and end time into a unique ID string, such that
    # the exact same start/end times will produce the same ID string.
    def get_id(self):
        if self.id is None:
            # if this object has a start and end time, we'll use both of them
            # to generate a unique timestamp, along with the class name
            hash_str = self.__class__.__name__ + "|"
            if self.get_field("time_start") is not None and \
               self.get_field("time_end") is not None:
                hash_str += "%s-%s" % (self.time_start.isoformat(),
                                       self.time_end.isoformat())
            elif self.get_field("timestamp") is not None:
                hash_str += "%s" % (self.timestamp.isoformat())
            else:
                assert False, "Cannot generate ID for GarminDatabaseEntryBase without `time_start`/`time_end` or `timestamp` fields."

            # encode the string to utf-8 and hash it
            data = hash_str.encode("utf-8")
            self.id = hashlib.sha256(data).hexdigest()
        return self.id


# ================================ Step Data ================================= #
# Represents a single database entry for Garmin step data.
class GarminDatabaseStepsEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
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

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "step_count"]


# ================================ Sleep Data ================================ #
class GarminDatabaseSleepMovementEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("movement_level",   [float], required=True),
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
            "movement_level": jdata["activityLevel"],
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "movement_level"]

class GarminDatabaseSleepHeartRateEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp", [datetime], required=True),
            ConfigField("heartrate", [int], required=True),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, timezone=None):
        timestamp = datetime.fromtimestamp(jdata["startGMT"] / 1000.0)
        if timezone is not None:
            timestamp = timestamp.astimezone(tz=timezone)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "heartrate": jdata["value"],
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "heartrate"]

# Represents a single database entry for Garmin sleep data.
class GarminDatabaseSleepEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("sleep_time_total_seconds", [int],      required=True),
            ConfigField("sleep_time_deep_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_light_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_rem_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_awake_seconds", [int], required=True),
            ConfigField("respiration_min", [float], required=True),
            ConfigField("respiration_max", [float], required=True),
            ConfigField("respiration_avg", [float], required=True),
            ConfigField("heartrate_resting", [int], required=True),
            ConfigField("movement", [GarminDatabaseSleepMovementEntry], required=False, default=None),
            ConfigField("heartrate", [GarminDatabaseSleepHeartRateEntry], required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, timezone=None):
        dto = jdata["dailySleepDTO"]

        # if the start or ending time is not defined, refuse to parse
        if dto["sleepStartTimestampGMT"] is None or \
           dto["sleepEndTimestampGMT"] is None:
            raise ValueError("Cannot parse Garmin sleep data without valid start/end times")

        time_start = datetime.fromtimestamp(dto["sleepStartTimestampGMT"] / 1000.0)
        time_end = datetime.fromtimestamp(dto["sleepEndTimestampGMT"] / 1000.0)
        if timezone is not None:
            time_start = time_start.astimezone(tz=timezone)
            time_end = time_end.astimezone(tz=timezone)

        # create an object by providing it with a JSON structure it can parse
        json_data = {
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "sleep_time_total_seconds": dto["sleepTimeSeconds"],
            "sleep_time_deep_sleep_seconds": dto["deepSleepSeconds"],
            "sleep_time_light_sleep_seconds": dto["lightSleepSeconds"],
            "sleep_time_rem_sleep_seconds": dto["remSleepSeconds"],
            "sleep_time_awake_seconds": dto["awakeSleepSeconds"],
            "respiration_min": dto["lowestRespirationValue"],
            "respiration_max": dto["highestRespirationValue"],
            "respiration_avg": dto["averageRespirationValue"],
            "heartrate_resting": jdata["restingHeartRate"],
        }

        # if movement data is available, include it
        if "sleepMovement" in jdata and \
           jdata["sleepMovement"] is not None and \
           len(jdata["sleepMovement"]) > 0:
            mdata = jdata["sleepMovement"]
            movement_entries = []
            for mentry in mdata:
                movement_entry = GarminDatabaseSleepMovementEntry.from_garmin_json(
                    mentry,
                    timezone=timezone
                )
                movement_entries.append(movement_entry.to_json())
            json_data["movement"] = movement_entries

        # if heartrate data is available, include it
        if "sleepHeartRate" in jdata and \
           jdata["sleepHeartRate"] is not None and \
           len(jdata["sleepHeartRate"]) > 0:
            hrdata = jdata["sleepHeartRate"]
            hr_entries = []
            for hre in hrdata:
                # if the heartrate value is missing, skip it
                if "value" not in hre or hre["value"] is None:
                    continue

                # parse the heartrate data into an entry object
                hr_entry = GarminDatabaseSleepHeartRateEntry.from_garmin_json(
                    hre,
                    timezone=timezone
                )
                hr_entries.append(hr_entry.to_json())
            json_data["heartrate"] = hr_entries

        # parse the final object from JSON, and generate its ID, before
        # returning it
        entry = cls.from_json(json_data)
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return [
            "id",
            "time_start",
            "time_end",
            "sleep_time_total_seconds",
            "sleep_time_deep_sleep_seconds",
            "sleep_time_light_sleep_seconds",
            "sleep_time_rem_sleep_seconds",
            "sleep_time_awake_seconds",
            "respiration_min",
            "respiration_max",
            "respiration_avg",
            "heartrate_resting",
        ]


# =============================== VO2Max Data ================================ #
# Represents a single database entry for Garmin vo2max data.
class GarminDatabaseVO2MaxEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp",       [datetime],  required=True),
            ConfigField("vo2max",          [float],     required=True),
            ConfigField("fitness_age",     [int],       required=True),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, timezone=None):
        # if the provided JSON object is a list, grab the first value
        if type(jdata) == list:
            assert len(jdata) > 0, "Garmin VO2Max JSON data is empty"
            jdata = jdata[0]

        # the "generic" field should be present; this contains the VO2Max data
        # points
        assert "generic" in jdata, "Garmin VO2Max JSON data missing \"generic\" field"
        gdata = jdata["generic"]

        # from within, get the calendar date and parse it as a datetime
        timestamp = datetime.strptime(gdata["calendarDate"], "%Y-%m-%d")
        if timezone is not None:
            timestamp = timestamp.astimezone(tz=timezone)

        # get the vo2max value (prefer the precise value, but if it's not
        # present, get the non-precise value
        vo2max = gdata.get("vo2MaxPreciseValue", None)
        if vo2max is None:
            vo2max = gdata.get("vo2MaxValue", None)

        # get the fitness age
        fitness_age = gdata["fitnessAge"]

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "vo2max": vo2max,
            "fitness_age": fitness_age,
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "timestamp", "vo2max", "fitness_age"]


# ============================= Database Objects ============================= #
# A configuration object for a database.
class GarminDatabaseConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("db_path",                  [str],      required=True),
        ]

# An object used to interact with a Garmin step database.
class GarminDatabase:
    def __init__(self, config: GarminDatabaseConfig):
        self.config = config
        self.table_steps_name = "steps"
        self.table_sleep_name = "sleep"
        self.table_vo2max_name = "vo2max"

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

    # Executes a search using `ORDER BY` to retrieve entries without needing a
    # specific condition to identify them.
    def search_order_by(self,
                        table: str,
                        order_by_column: str,
                        desc: bool = False,
                        limit: int = None):
        cmd = "SELECT * FROM %s ORDER BY %s" % (table, order_by_column)
        if desc:
            cmd += " DESC"
        if limit is not None:
            cmd += " LIMIT %d" % limit

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

    # Returns the entry with the latest `time_end` timestamp, or `None` if
    # there are no entries.
    def search_steps_latest(self):
        result = self.search_order_by(
            self.table_steps_name,
            order_by_column="time_end",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3(row)
            return entry
        return None

    # ------------------------------ Step Data ------------------------------- #
    # Inserts the provided entry into the database.
    def save_sleep(self, entry: GarminDatabaseSleepEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseSleepEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_sleep_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_sleep_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_sleep_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_sleep_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple sleep entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for sleep entries within the given time range.
    def search_sleep_by_time_range(self, time_start: datetime, time_end: datetime):
        condition = "time_start >= %.f AND time_end <= %.f" % (
            time_start.timestamp(),
            time_end.timestamp()
        )
        result = self.search(self.table_sleep_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest `time_end` timestamp, or `None` if
    # there are no entries.
    def search_sleep_latest(self):
        result = self.search_order_by(
            self.table_sleep_name,
            order_by_column="time_end",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3(row)
            return entry
        return None

    # ----------------------------- VO2Max Data ------------------------------ #
    # Inserts the provided entry into the database.
    def save_vo2max(self, entry: GarminDatabaseVO2MaxEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseVO2MaxEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_vo2max_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_vo2max_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_vo2max_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_vo2max_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple vo2max entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for vo2max entries within the given time range.
    def search_vo2max_by_day(self, timestamp: datetime):
        condition = "timestamp >= %.f AND timestamp <= %.f" % (
            dtu.set_time_beginning_of_day(timestamp),
            dtu.set_time_end_of_day(timestamp),
        )
        result = self.search(self.table_vo2max_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest timestamp, or `None` if there are no
    # entries.
    def search_vo2max_latest(self):
        result = self.search_order_by(
            self.table_vo2max_name,
            order_by_column="timestamp",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3(row)
            return entry
        return None

