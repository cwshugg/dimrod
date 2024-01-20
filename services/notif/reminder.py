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
            ConfigField("send_ntfys",       [list],     required=False,     default=[]),
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
                   str(self.send_ntfys) + \
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
    
