# This file defines base classes for the 'Life Tracker', which is a task I am
# implementing to track my habits, health, and lifestyle.

# Imports
import os
import sys
from datetime import datetime
import inspect
from enum import Enum
import sqlite3

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(os.path.dirname(fdir)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob
from lib.uniserdes import Uniserdes, UniserdesField
from lib.oracle import OracleSession
import lib.dtu as dtu
from lib.db import Database, DatabaseConfig

class LifeMetricTrigger(Uniserdes):
    """A class representing the trigger conditions for a LifeMetric to be sent to
    the user. These work similar to the Notif service's reminder objects.
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("years",        [list],  required=False, default=[]),
            UniserdesField("months",       [list],  required=False, default=[]),
            UniserdesField("days",         [list],  required=False, default=[]),
            UniserdesField("weekdays",     [list],  required=False, default=[]),
            UniserdesField("hours",        [list],  required=False, default=[]),
            UniserdesField("minutes",      [list],  required=False, default=[]),
            UniserdesField("cooldown",     [int],   required=False, default=3600),
        ]

    def parse_json(self, jdata: dict):
        result = super().parse_json(jdata)
        self.check_triggers()
        return result

    def check_triggers(self):
        """Function borrowed from notif's Reminder object that makes sure all
        trigger values are within the expected range.
        """
        for y in self.years:
            assert type(y) == int, "years must be a list of ints"
        for m in self.months:
            assert type(m) == int, "months must be a list of ints"
            assert m in range(1, 13), "months must be within 1-12"
        for d in self.days:
            assert type(d) == int, "days must be a list of ints"
            assert d in range(1, 32), "days must be within 1-31"
        for wd in self.weekdays:
            assert type(wd) == int, "weekdays must be a list of ints"
            assert wd in range(1, 8), "weekdays must be within 1-7"
        for h in self.hours:
            assert type(h) == int, "hours must be a list of ints"
            assert h in range(0, 24), "hours must be within 0-23"
        for m in self.minutes:
            assert type(m) == int, "minutes must be a list of ints"
            assert m in range(0, 60), "minutes must be within 0-59"
        assert self.cooldown > 0, "cooldown must be a positive integer"

    def is_ready(self, dt: datetime = None, last_trigger: datetime = None):
        """Returns True if the given datetime (which defaults to the current
        datetime) matches the trigger conditions.
        """
        if dt is None:
            dt = datetime.now()
        result = True

        # check for matching years
        if len(self.years) > 0:
            result = result and any(y == dt.year for y in self.years)

        # check for matching months
        if len(self.months) > 0:
            result = result and any(m == dt.month for m in self.months)

        # check for matching days
        if len(self.days) > 0:
            result = result and any(d == dt.day for d in self.days)

        # check for matching weekdays
        if len(self.weekdays) > 0:
            result = result and any(dtu.Weekday(wd) == dtu.get_weekday(dt) for wd in self.weekdays)

        # check for matching hours
        if len(self.hours) > 0:
            result = result and any(h == dt.hour for h in self.hours)

        # check for matching minutes
        if len(self.minutes) > 0:
            result = result and any(m == dt.minute for m in self.minutes)

        # was a "last triggered" time provided? If so, make sure we are the
        # cooldown point.
        if last_trigger is not None:
            result = result and dtu.diff_in_seconds(dt, last_trigger) > self.cooldown

        return result

class LifeMetricValue(Uniserdes):
    """A class representing a single choice/value for a metric."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name",         [str],  required=True),
            UniserdesField("title",        [str],  required=True),
            UniserdesField("score_points", [int],  required=False, default=0),
        ]

    def get_sqlite3_column_name_selection_count(self):
        return "%s__selection_count" % self.name

    def get_sqlite3_column_name_score_per_count(self):
        return "%s__score_per_count" % self.name

class LifeMetric(Uniserdes):
    """A class representing a single metric. One metric corresponds to one menu in
    Telegram, where the user selects one of N possible answers, and the result is
    logged in a database with a timestamp.

    One metric contains several metric values, each of which has a score that is
    awarded to the user if the option is selected.
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name",         [str],                  required=True),
            UniserdesField("title",        [str],                  required=True),
            UniserdesField("values",       [LifeMetricValue],      required=True),
            UniserdesField("trigger",      [LifeMetricTrigger],    required=True),
            UniserdesField("telegram_menu_timeout", [int],         required=False, default=90000),
            UniserdesField("telegram_menu_behavior_type", [str],   required=False, default="SINGLE_CHOICE"),
        ]

    def has_nonzero_scores(self):
        """Returns True if any one of the possible options has a non-zero score.

        Returns False is *all* options have a score of zero.
        """
        for v in self.values:
            if v.score_points != 0:
                return True
        return False

    def get_score_max(self, minimum: int = None):
        """Iterates through the metric's values and determines the maximum possible
        score that could be achieved.

        The `minimum` field can be set to force the value returned from this
        function to never drop below `minimum`. This handy, for example, for
        ensuring a life metric with *negative* scores never returns a maximum
        score less than zero.
        """
        result = max([v.score_points for v in self.values])

        # if a minimum value is set, make sure we aren't below it
        if minimum is not None:
            return minimum
        return result

    def get_telegram_menu(self, title_prefix: str = ""):
        """Returns a JSON object used to represent a telegram menu containing the
        metric's title and all of its values.
        """
        menu = {
            "title": title_prefix + self.title,
            "timeout": self.telegram_menu_timeout,
            "behavior_type": self.telegram_menu_behavior_type,
            "options": []
        }

        # add each metric value as an option
        for val in self.values:
            jdata = {
                "title": val.title
            }
            menu["options"].append(jdata)

        return menu

# ============================= Database Entries ============================= #
class LifeMetricEntryStatus(Enum):
    """An enum class used to represent the status of a database entry for a metric."""
    # Alive: the Telegram menu is still open and its values can be modified.
    ALIVE = 0

    # Dead: the Telegram menu has expired and its values can no longer be
    # modified.
    DEAD = 1

class LifeMetricEntry(Uniserdes):
    """A class representing a single database entry for a metric."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("timestamp",    [datetime],  required=False, default=datetime.now()),
            UniserdesField("status",       [LifeMetricEntryStatus], required=False, default=LifeMetricEntryStatus.ALIVE),
            UniserdesField("metric_name", [str], required=False, default=None),
            UniserdesField("telegram_menu_id", [str], required=False, default=None)
        ]

    def init_from_metric(self, metric: LifeMetric):
        """Takes a metric and modifies the entry to contain fields that match the
        metric's values. This is needed in order to form a SQL entry.
        """
        self.metric_name = metric.name

        # add new fields to the object for each of the metric's values
        for value in metric.values:
            score_per_count_name = value.get_sqlite3_column_name_score_per_count()
            selection_count_name = value.get_sqlite3_column_name_selection_count()

            self.fields += [
                UniserdesField(score_per_count_name, [int], required=True),
                UniserdesField(selection_count_name, [int], required=True)
            ]

            # set the fields to hold default values
            setattr(self, score_per_count_name, value.score_points)
            setattr(self, selection_count_name, 0)

    def init_from_telegram_menu(self, menu: dict):
        """Initializes certain fields of the object from the provided Telegram menu."""
        self.telegram_menu_id = menu["id"]

    def get_sqlite3_fields_to_keep_visible(self):
        return [f.name for f in self.fields]

    def get_sqlite3_table_definition(self, table_name: str):
        # keep all fields visible in the database
        field_names = self.get_sqlite3_fields_to_keep_visible()
        return super().get_sqlite3_table_definition(
            table_name,
            fields_to_keep_visible=field_names,
            primary_key_field="telegram_menu_id"
        )

    def to_sqlite3(self):
        # keep all fields visible in the database
        field_names = self.get_sqlite3_fields_to_keep_visible()
        return super().to_sqlite3(fields_to_keep_visible=field_names)


# ========================== Main LifeTracker Class ========================== #
class LifeTracker(Uniserdes):
    """A class reprsenting a collection of life metrics, along with other metadata."""
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("metrics",      [LifeMetric],   required=True),
            UniserdesField("db_path",      [str],          required=True),
            UniserdesField("telegram_chat_id", [str],      required=True),
        ]

    def get_database(self):
        """Returns a `Database` wrapper interface to interact with the tracker's
        database.
        """
        config = DatabaseConfig.from_json({
            "path": self.db_path
        })
        return Database(config)

    def get_metric_by_name(self, name: str):
        """Retrieves a metric, given its name."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def save_metric_entry(self, entry: LifeMetricEntry):
        """Saves the given entry to the database."""
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        # make sure the table exists for the metric
        table_name = entry.metric_name
        cur.execute(entry.get_sqlite3_table_definition(table_name))

        # insert the entry into the table
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (table_name, str(entry.to_sqlite3())))

        con.commit()
        con.close()

    def get_table_exists(self, name: str):
        """Returns True if a table exists."""
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cmd = "SELECT name FROM sqlite_master WHERE type='table' AND name='%s'" % \
              name
        for _ in cur.execute(cmd):
            con.close()
            return True

        con.close()
        return False

    def get_alive_metrics(self):
        """Queries the database and returns a list of `LifeMetricEntry` objects,
        representing the entries that are still "alive" (i.e. whose Telegram
        menus have not yet expired).
        """
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        # perform the following for all metrics
        entries = []
        for metric in self.metrics:
            # first, determine if the table exists. If it doesn't, we can skip
            # this iteration
            if not self.get_table_exists(metric.name):
                continue

            cmd = "SELECT * FROM %s WHERE status == %d" % \
                  (metric.name, LifeMetricEntryStatus.ALIVE.value)
            result = cur.execute(cmd)

            for data in result:
                # convert back from a sqlite3 tuple
                entry = LifeMetricEntry()
                entry.init_from_metric(metric)
                entry.parse_sqlite3(
                    data,
                    fields_kept_visible=entry.get_sqlite3_fields_to_keep_visible()
                )
                entries.append(entry)

        con.close()
        return entries

    def delete_metric(self, entry: LifeMetricEntry):
        """Deletes the given `LifeMetricEntry` from the database."""
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        table_name = entry.metric_name
        cmd = "DELETE FROM %s WHERE telegram_menu_id == \"%s\"" % \
              (table_name, entry.telegram_menu_id)
        cur.execute(cmd)

        # commit the changes and close the connection
        con.commit()
        con.close()

    def get_latest_metric_entry(self, metric: LifeMetric):
        """Returns the latest (according to timestamp) database entry for the
        provided metric.
        """
        # if the metric's table doesn't exist yet, return early
        if not self.get_table_exists(metric.name):
            return None

        # set up a connection to the database
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        # build a command to grab the entry with the highest integer timestamp
        # (which will be the latest time)
        table_name = metric.name
        cmd = "SELECT * FROM %s ORDER BY timestamp DESC LIMIT 1" % table_name
        result = cur.execute(cmd)

        entry = None
        for data in result:
            entry = LifeMetricEntry()
            entry.init_from_metric(metric)
            entry.parse_sqlite3(
                data,
                fields_kept_visible=entry.get_sqlite3_fields_to_keep_visible()
            )
            break # there should only be one result (see `LIMIT 1` above)

        con.close()
        return entry

    def get_metric_entries_by_timestamp(self,
                                        metric: LifeMetric,
                                        begin: datetime = None,
                                        end: datetime = None):
        """Takes in a LifeMetric and two timestamps, and returns a list of all
        metric entries that fall between the beginning and ending timestamps.

        If `begin` is left as `None`, all entries leading up to
        `end` will be returned.

        If `end` is left as `None`, all entries occurring after
        `begin` will be returned.

        If both timestamps are `None`, an error is thrown.
        """
        assert begin is not None or \
               end is not None, \
               "At least one timestamp must be provided"

        # if the metric's table doesn't exist yet, return early
        if not self.get_table_exists(metric.name):
            return []

        # set up a connection to the database
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        # build a command to grab all relevant entries
        table_name = metric.name
        cmd = "SELECT * FROM %s WHERE " % table_name
        cmd_conditions = []
        if begin is not None:
            cmd_conditions.append("timestamp >= %f" % begin.timestamp())
        if end is not None:
            cmd_conditions.append("timestamp < %f" % end.timestamp())
        cmd += " AND ".join(cmd_conditions)
        cmd += " ORDER BY timestamp DESC"

        # execute the command and iterate through the results
        result = cur.execute(cmd)
        entries = []
        for data in result:
            entry = LifeMetricEntry()
            entry.init_from_metric(metric)
            entry.parse_sqlite3(
                data,
                fields_kept_visible=entry.get_sqlite3_fields_to_keep_visible()
            )
            entries.append(entry)

        con.close()
        return entries


# =============================== Base TaskJob =============================== #
class TaskJob_LifeTracker(TaskJob):
    """Base class for a LifeTracker task job. Implements some common functionality
    useful for all subclasses.
    """
    def init(self):
        """Overridden initialization function."""
        self.refresh_rate = 5 * 60 # updates every 5 minutes
        self.config_name = os.path.basename(__file__).replace(".py", ".json")

    def get_config_path(self):
        """Returns the path to where the JSON config file is expected to be."""
        this_file = inspect.getfile(self.__class__)
        config_dir = os.path.dirname(os.path.realpath(this_file))
        return os.path.join(config_dir, self.config_name)

    def get_tracker(self):
        """Parses the tracker config file and returns a `LifeTracker` object."""
        tracker = LifeTracker()
        tracker.parse_file(self.get_config_path())
        return tracker

    # -------------------------- Telegram Interface -------------------------- #
    def get_telegram_session(self):
        """Creates and returns an authenticated OracleSession with the telegram bot."""
        s = OracleSession(self.service.config.telegram)
        s.login()
        return s

    def send_message(self, tracker: LifeTracker, text: str):
        """Sends a message to Telegram."""
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        payload = {
            "chat_id": tracker.telegram_chat_id,
            "text": text,
        }
        r = telegram_session.post("/bot/send/message", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to send message via Telegram: %s" % \
               telegram_session.get_response_message(r)

        message = telegram_session.get_response_json(r)
        return message

    def send_metric_menu(self, tracker: LifeTracker, metric: LifeMetric):
        """Sends LifeMetric to be completed via a Telegram menu."""
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        payload = {
            "chat_id": tracker.telegram_chat_id,
            "menu": metric.get_telegram_menu(title_prefix="❓ "),
        }
        r = telegram_session.post("/bot/send/menu", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to create menu via Telegram: %s" % \
               telegram_session.get_response_message(r)

        # parse the payload JSON in the response as a menu and return it (this
        # will contain the menu's ID, and other new information)
        created_menu = telegram_session.get_response_json(r)
        return created_menu

    def get_metric_entry_menu(self, entry: LifeMetricEntry):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        payload = {"menu_id": entry.telegram_menu_id}
        r = telegram_session.post("/bot/get/menu", payload=payload)

        # if the message returned unsuccessful, assume the menu no longer
        # exists
        if not telegram_session.get_response_success(r):
            return None

        return telegram_session.get_response_json(r)

