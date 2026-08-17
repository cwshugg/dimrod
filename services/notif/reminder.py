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
from lib.uniserdes import Uniserdes, UniserdesField
# The reminder's schedule is now expressed with the shared DatetimeTrigger type
# (from lib.dtu) rather than six flat `trigger_*` fields. Trigger-matching logic
# is delegated to DatetimeTrigger instead of being re-implemented here.
from lib.dtu import DatetimeTrigger


# ========================== TelegramTarget Object ========================== #
class TelegramTarget(Uniserdes):
    """Represents a single Telegram delivery target for a reminder: a chat
    id/name plus an optional forum topic id (message_thread_id).

    The `chat` value keeps the same matching semantics as the old bare-string
    entry (notif matches by chat id OR substring of the chat name). The
    `topic` value, when present, is the forum topic (message_thread_id) the
    reminder should fire into; when absent/None it means General / no topic.
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("chat",  [str],      required=True),
            UniserdesField("topic", [str, int], required=False, default=None),
        ]

    def __repr__(self):
        """Deterministic representation derived solely from this target's field
        values.

        `Reminder.get_id()` hashes `str(self.send_telegrams)`, which relies on
        each target's repr. The default object repr embeds the instance's
        memory address, which would make reminder IDs random per-process and
        break dedupe. Deriving the repr purely from the field values keeps a
        reminder's identity stable across parses and processes.
        """
        return "TelegramTarget(chat=%r,topic=%r)" % (self.chat, self.topic)

    def __str__(self):
        return self.__repr__()


# ============================= Reminder Object ============================== #
class Reminder(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("message",          [str],      required=True),
            UniserdesField("title",            [str],      required=False,     default="Reminder"),
            UniserdesField("send_telegrams",   [TelegramTarget], required=False, default=[]),
            UniserdesField("send_emails",      [list],     required=False,     default=[]),
            UniserdesField("send_ntfys",       [list],     required=False,     default=[]),
            UniserdesField("trigger",          [DatetimeTrigger], required=False, default=DatetimeTrigger.from_json({})),
            UniserdesField("id",               [str],      required=False,     default=None)
        ]

    def __str__(self):
        """String representation"""
        return "[R-%s] %s: %s" % (self.get_id(), self.title, self.message)

    def get_trigger_str(self):
        # Build a human-readable summary from the nested DatetimeTrigger's
        # fields. After parsing, `months` are `Month` enums and `weekdays` are
        # `Weekday` enums (see lib.dtu), so render those by their capitalized
        # names; all other fields are plain integers.
        t = self.trigger
        parts = []
        if len(t.years) > 0:
            parts.append("Years: " + ", ".join([str(y) for y in t.years]))
        if len(t.months) > 0:
            parts.append("Months: " + ", ".join([m.name.title() for m in t.months]))
        if len(t.days) > 0:
            parts.append("Days: " + ", ".join([str(d) for d in t.days]))
        if len(t.weekdays) > 0:
            parts.append("Weekdays: " + ", ".join([wd.name.title() for wd in t.weekdays]))
        if len(t.hours) > 0:
            parts.append("Hours: " + ", ".join([str(h) for h in t.hours]))
        if len(t.minutes) > 0:
            parts.append("Minutes: " + ", ".join([str(m) for m in t.minutes]))
        return "; ".join(parts)

    def get_id(self):
        """Returns the reminder's unique ID string. (If one hasn't been set, this
        generates one.)
        """
        if self.id is None:
            h = hashlib.sha256()
            t = self.trigger
            trigger_years = list(t.years)
            trigger_months = [m.value for m in t.months]
            trigger_days = list(t.days)
            trigger_weekdays = [wd.value for wd in t.weekdays]
            trigger_hours = list(t.hours)
            trigger_minutes = list(t.minutes)
            text = self.message + \
                   str(self.send_telegrams) + \
                   str(self.send_emails) + \
                   str(self.send_ntfys) + \
                   str(trigger_years) + \
                   str(trigger_months) + \
                   str(trigger_days) + \
                   str(trigger_weekdays) + \
                   str(trigger_hours) + \
                   str(trigger_minutes)
            h.update(text.encode("utf-8"))
            self.id = h.hexdigest()
        return self.id

    # ------------------------------- Triggers ------------------------------- #
    def check_triggers(self):
        """Checks the values of each trigger to ensure it's in a valid range."""
        self.trigger.check_fields()

    def ready(self):
        """Returns True if all trigger conditions are satisfied.

        The per-field matching logic (year/month/day/weekday/hour/minute,
        including negative "day-from-end-of-month" handling) is delegated to
        DatetimeTrigger.matches().
        """
        return self.trigger.matches(datetime.now())

    def expired(self):
        """Returns True if the reminder will never be triggered again."""
        now = datetime.now()

        # Read the schedule from the nested DatetimeTrigger. Months are stored
        # as `Month` enums, so use their underlying ints (`.value`) for the
        # numeric comparisons below; years and days are already plain ints.
        trigger_years = list(self.trigger.years)
        trigger_months = [m.value for m in self.trigger.months]
        trigger_days = list(self.trigger.days)

        # if no year is defined, then immediately return false (by default, all
        # reminders will repeat annually unless a year is specified)
        if len(trigger_years) == 0:
            return False

        # otherwise, if all the defined years have been passed, it's expired
        highest_year = max(trigger_years)
        if highest_year < now.year: # highest year has passed
            return True
        if highest_year > now.year: # highest year is still coming
            return False

        # if no month or days are defined, we'll keep it around
        has_months = len(trigger_months) > 0
        has_days = len(trigger_days) > 0
        if not has_months and not has_days:
            return False

        # if months are defined, find the highest one and determine if it's
        # passed yet
        if has_months:
            highest_month = max(trigger_months)
            if highest_month < now.month:
                return True

        # check if the day has passed, if days are defined
        if has_days:
            highest_day = max(trigger_days)
            # if no months are defined and its the last month of the year, check
            # if the last occurrence of that day has passed
            if not has_months and now.month == 12 and highest_day < now.day:
                return True
            elif has_months:
                highest_month = max(trigger_months)
                return highest_month <= now.month and highest_day < now.day

        # otherwise, we'll say it's not expired. This doesn't cover several
        # corner cases, but it's safe enough to use for periodically cleaning
        # out expired reminders
        return False

