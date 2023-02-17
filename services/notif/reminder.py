# This module defines the Reminder object, representative of a single reminder
# event that this service is responsible for monitoring.

# Imports
import os
import sys
import json
import flask
import hashlib
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ============================= Reminder Object ============================== #
class Reminder(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("message",          [str],      required=True),
            ConfigField("title",            [str],      required=False,     default="Reminder"),
            ConfigField("send_telegrams",   [list],     required=False,     default=[]),
            ConfigField("send_emails",      [list],     required=False,     default=[]),
            ConfigField("trigger_years",    [list],     required=False,     default=[]),
            ConfigField("trigger_months",   [list],     required=False,     default=[]),
            ConfigField("trigger_days",     [list],     required=False,     default=[]),
            ConfigField("trigger_weekdays", [list],     required=False,     default=[]),
            ConfigField("trigger_hours",    [list],     required=False,     default=[]),
            ConfigField("trigger_minutes",  [list],     required=False,     default=[]),
            ConfigField("id",               [str],      required=False,     default=None)
        ]

    # String representation
    def __str__(self):
        return "[R-%s] %s: %s" % (self.get_id(), self.title, self.message)
    
    # Returns the reminder's unique ID string. (If one hasn't been set, this
    # generates one.)
    def get_id(self):
        if self.id is None:
            h = hashlib.sha256()
            text = self.message + \
                   str(self.send_telegrams) + \
                   str(self.send_emails) + \
                   str(self.trigger_years) + \
                   str(self.trigger_months) + \
                   str(self.trigger_days) + \
                   str(self.trigger_weekdays) + \
                   str(self.trigger_hours) + \
                   str(self.trigger_minutes)
            h.update(text.encode("utf-8"))
            self.id = h.hexdigest()
        return self.id
    
    # ------------------------------- Triggers ------------------------------- #
    # Checks the values of each trigger to ensure it's in a valid range.
    def check_triggers(self):
        for y in self.trigger_years:
            assert type(y) == int, "trigger_years must be a list of ints"
        for m in self.trigger_months:
            assert type(m) == int, "trigger_months must be a list of ints"
            assert m in range(1, 13), "trigger_months must be within 1-12"
        for d in self.trigger_days:
            assert type(d) == int, "trigger_days must be a list of ints"
            assert d in range(1, 32), "trigger_days must be within 1-31"
        for wd in self.trigger_weekdays:
            assert type(wd) == int, "trigger_weekdays must be a list of ints"
            assert wd in range(1, 8), "trigger_weekdays must be within 1-7"
        for h in self.trigger_hours:
            assert type(h) == int, "trigger_hours must be a list of ints"
            assert h in range(0, 24), "trigger_hours must be within 0-23"
        for m in self.trigger_minutes:
            assert type(m) == int, "trigger_minutes must be a list of ints"
            assert m in range(0, 60), "trigger_minutes must be within 0-59"

    # Returns True if all trigger conditions are satisfied.
    def ready(self):
        now = datetime.now()
        result = True
            
        # YEAR CHECK
        if len(self.trigger_years) > 0:
            year_ok = False
            for y in self.trigger_years:
                if y == now.year:
                    year_ok = True
                    break
            result = result and year_ok

        # MONTH CHECK
        if len(self.trigger_months) > 0:
            month_ok = False
            for m in self.trigger_months:
                if m == now.month:
                    month_ok = True
                    break
            result = result and month_ok

        # DAY CHECK
        if len(self.trigger_days) > 0:
            day_ok = False
            for d in self.trigger_days:
                if d == now.day:
                    day_ok = True
                    break
            result = result and day_ok

        # WEEKDAY CHECK
        if len(self.trigger_weekdays) > 0:
            # the datetime library considers monday to be the start of the week,
            # but my code will consider sunday to be the start of the week
            current_weekday = ((now.weekday() + 1) % 7) + 1
            weekday_ok = False
            for wd in self.trigger_weekdays:
                if wd == current_weekday:
                    weekday_ok = True
                    break
            result = result and weekday_ok

        # HOUR CHECK
        if len(self.trigger_hours) > 0:
            hour_ok = False
            for h in self.trigger_hours:
                if h == now.hour:
                    hour_ok = True
                    break
            result = result and hour_ok

        # MINUTE CHECK
        if len(self.trigger_minutes) > 0:
            minute_ok = False
            for m in self.trigger_minutes:
                if m == now.minute:
                    minute_ok = True
                    break
            result = result and minute_ok

        return result
    
    # Returns True if the reminder will never be triggered again.
    def expired(self):
        now = datetime.now()

        # if no year is defined, then immediately return false (by default, all
        # reminders will repeat annually unless a year is specified)
        if len(self.trigger_years) == 0:
            return False
            
        # otherwise, if all the defined years have been passed, it's expired
        highest_year = max(self.trigger_years)
        if highest_year < now.year: # highest year has passed
            return True
        if highest_year > now.year: # highest year is still coming
            return False

        # if no month or days are defined, we'll keep it around
        has_months = len(self.trigger_months) > 0
        has_days = len(self.trigger_days) > 0
        if not has_months and not has_days:
            return False
        
        # if months are defined, find the highest one and determine if it's
        # passed yet
        if has_months:
            highest_month = max(self.trigger_months)
            if highest_month < now.month:
                return True
        
        # check if the day has passed, if days are defined
        if has_days:
            highest_day = max(self.trigger_days)
            # if no months are defined and its the last month of the year, check
            # if the last occurrence of that day has passed
            if not has_months and now.month == 12 and highest_day < now.day:
                return True
            elif has_months:
                highest_month = max(self.trigger_months)
                return highest_month <= now.month and highest_day < now.day

        # otherwise, we'll say it's not expired. This doesn't cover several
        # corner cases, but it's safe enough to use for periodically cleaning
        # out expired reminders
        return False

    # --------------------------- JSON Conversion ---------------------------- #
    # Inherited JSON-parsing function.
    @staticmethod
    def from_json(jdata: dict):
        result = super().from_json(jdata)
        result.check_triggers()
        return result
    
    

# ============================= List Item Class ============================== #
class ScribbleListItem:
    # Constructor.
    def __init__(self, text: str, iid=None):
        self.iid = iid
        self.text = text
        self.get_id()

    # String representation.
    def __str__(self):
        return "%s: %s" % (self.get_id(), self.text)
    
    # Returns the list item's unique ID string.
    def get_id(self):
        # if the IID isn't defined, generate one on-demand
        if self.iid is None:
            h = hashlib.sha256()
            h.update(self.text.encode("utf-8"))
            h.update(os.urandom(16))
            self.iid = h.hexdigest()
        return self.iid

    # -------------------------- SQLite3 Conversion -------------------------- #
    # Returns a string representation of the object, as a tuple, to be used in a
    # SQLite3 command.
    def to_sqlite3_str(self):
        return str(self.to_tuple())
    
    # Converts the object's fields into a tuple readable by SQLite3.
    def to_tuple(self):
        return (self.get_id(), self.text)
    
    # Converts a given tuple into a ScribbleListItem object.
    @staticmethod
    def from_tuple(t: tuple):
        assert len(t) >= 2, "not enough fields in the given tuple"
        assert type(t[0]) == str, "first tuple field must be a string"
        assert type(t[1]) == str, "second tuple field must be a string"
        return ScribbleListItem(t[1], iid=t[0])

    # --------------------------- JSON Conversion ---------------------------- #
    # Converts the object to JSON and returns it.
    def to_json(self):
        return {"iid": self.get_id(), "text": self.text}


# ================================ List Class ================================ #
class ScribbleList:
    # Constructor.
    def __init__(self, path: str):
        self.path = path
        self.db_init()

   # ------------------------------- Helpers -------------------------------- #
    # Converts a file path to a list name.
    @staticmethod
    def file_to_name(path: str):
        file = os.path.basename(path).lower().replace(" ", "_")
        return file.replace(".db", "")
    
    # Converts a list name to a file path.
    @staticmethod
    def name_to_file(name: str, dirpath: str):
        path = os.path.join(dirpath, name.lower().replace(" ", "_"))
        return path + ".db" 

    # ------------------------------ Interface ------------------------------- #

    # Returns all list items in the list.
    def get_all(self):
        return self.db_command("SELECT * FROM items", fetch=True)
    
    # Looks for an entry given the ID. Returns a single item, or None.
    def search_by_id(self, iid: str):
        result = self.db_command("SELECT * FROM items WHERE iid='%s'" % iid, fetch=True)
        return None if len(result) == 0 else result[0]
    
    # Looks for items that contain the given text. Returns a filled or empty
    # list.
    def search_by_text(self, text: str):
        command = "SELECT * FROM items WHERE " \
                  "description LIKE '%%%s%%' OR " \
                  "description LIKE '%s%%' OR " \
                  "description LIKE '%%%s' OR " \
                  "description LIKE '%s'" % \
                  (text, text, text, text)
        return self.db_command(command, fetch=True)
    
    # Adds a new item to the list. Throws an exception if the item already
    # exists in the list.
    def add(self, item: ScribbleListItem):
        self.db_command("INSERT INTO items VALUES %s" % item.to_sqlite3_str(), commit=True)
    
    # Removes the given item from the list. Throws an exception if the item
    # isn't found in the list.
    def remove(self, item: ScribbleListItem):
        assert self.search_by_id(item.get_id()) is not None, \
               "an item with ID \"%s\" could not be found" % item.get_id()
        self.db_command("DELETE FROM items WHERE iid='%s'" % item.get_id(), commit=True)

    # -------------------------- SQLite3 Interface --------------------------- #
    # Initializes the database.
    def db_init(self):
        # if the file doesn't exist, create it
        if not os.path.isfile(self.path):
            fp = open(self.path, "w")
            fp.close()
        
        # open a database connection and get a cursor
        connection = sqlite3.connect(self.path)
        c = connection.cursor()

        # create a table, if it doesn't exist, to contain the list's items
        c.execute("CREATE TABLE IF NOT EXISTS items "
                  "(iid TEXT PRIMARY KEY, description TEXT)")

        # commit changes and disconnect
        connection.commit()
    
    # Runs a generic command, performing commit() or fetchall() accordingly.
    def db_command(self, cmd: str, commit=False, fetch=False):
        self.db_init()
        connection = sqlite3.connect(self.path)
        c = connection.cursor()

        # execute the given command then commit
        result = c.execute(cmd)
        if commit:
            connection.commit()
        if fetch:
            items = result.fetchall()
            result = []
            for i in items:
                result.append(ScribbleListItem.from_tuple(i))
            return result
        return []

    # --------------------------- JSON Conversion ---------------------------- #
    # Converts the list to a JSON object and returns it.
    def to_json(self):
        result = {"name": self.file_to_name(self.path), "items": []}
        items = self.get_all()
        for i in items:
            result["items"].append(i.to_json())
        return result

