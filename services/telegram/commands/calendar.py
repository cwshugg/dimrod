# Implements the /calendar bot command.

# Imports
import os
import sys
import re
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from lib.dtu import *
from lib.google.google_calendar import GoogleCalendar, GoogleCalendarConfig


# ================================= Helpers ================================== #
# Parses the calendar event's starting & ending datetimes from the user's
# arguments.
def parse_datetimes(args: list):
    # TODO
    return None

# Parses the calendar event's title from the user's arguments.
def parse_title(args: list):
    # TODO
    return None

# Parses the calendar event's description from the user's arguments.
def parse_description(args: list):
    # TODO - retrieve all text past the end of the period after the event title


# =================================== Main =================================== #
def command_calendar(service, message, args: list):
    if len(args) < 2:
        msg = "Use this to create calendar events. Here's are some examples:\n\n" \
              "• <code>/calendar Friday 8am 8:45am. Doctor's Appointment</code>\n" \
              "• <code>/calendar 2030-07-04 1pm 2pm. Work Lunch. You're eating out with your team!</code>\n" \
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # TODO

