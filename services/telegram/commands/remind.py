# Implements the /remind bot command.

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


# ================================= Helpers ================================== #
# Returns a weekday number based on the given text. Returns None if the string
# isn't recognized.
def parse_weekday(text: str):
    tl = text.strip().lower()
    weekdays = [
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday"
    ]
    for i in range(len(weekdays)):
        if tl in weekdays[i]:
            return i + 1
    return None

# Converts Python's monday-first weekday encoding to my sunday-first encoding.
def get_weekday(dt: datetime):
    return ((dt.weekday() + 1) % 7) + 1

# Parses a suffixed time offset string (ex: "1w", "2d", "3h", "4m"). Returns 0
# if nothing is recognized.
def parse_time_offset(text: str):
    suffixes = {
        "w": 604800,        # one week (in seconds)
        "d": 86400,         # one day (in seconds)
        "h": 3600,          # one hour (in seconds)
        "m": 60             # one minute (in seconds)
    }
    for suffix in suffixes:
        if not text.endswith(suffix):
            continue
        # parse digits from the string and return the offset
        multiplier = float(re.findall("\d+", text)[0])
        return multiplier * suffixes[suffix]
    return 0.0

# Parses the datetime from the user's arguments.
def parse_datetime(args: list):
    # Takes in a value (float or int) and adds/subtracts it from the datetime
    # (by converting to timestamp). The new datetime is returned.
    def adjust_dt(d: datetime, value):
        return datetime.fromtimestamp(d.timestamp() + value)
    
    # start with the current date/time
    dt = datetime.now()
    
    # iterate through the arguments and look for keywords
    for arg in args:
        # look for mention of a weekday
        wd = parse_weekday(arg)
        if wd is not None:
            # increase the current datetime until it lines up with the specified
            # weekday
            dt = adjust_dt(dt, 86400)
            while get_weekday(dt) != wd:
                dt = adjust_dt(dt, 86400)

        # look for suffixed time offsets ("1d", "2h", "3m")
        offset = parse_time_offset(arg)
        dt = adjust_dt(dt, offset)
    return dt

# Parses a reminder message from the user's arguments.
def parse_message(args: list):
    return " ".join(args)


# =================================== Main =================================== #
def command_remind(service, message, args: list):
    if len(args) < 2:
        msg = "Use this to set up reminders. Here's an example:\n\n" \
              "<code>/remind 1d 3h. Take out the trash!</code>"
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # find where a "." appears first in the arguments. This is where we'll
    # separate datetime and message
    all_args = " ".join(args[1:])
    pieces = all_args.split(".")
    dt_args = pieces[0].split()
    msg_args = [] if len(pieces) < 2 else ". ".join(pieces[1:]).split()

    # if we're missing either group of args, send back an error
    if len(dt_args) == 0:
        msg = "You need to specify some sort of date/time indicator " \
              "<i>before</i> the period in your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return
    if len(msg_args) == 0:
        msg = "You need to specify a message <i>after</i> the period in " \
              "your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # parse the arguments as a reminder
    dt = parse_datetime(dt_args)
    msg = parse_message(msg_args)

    # create a HTTP session with notif
    session = OracleSession(service.config.notif_address,
                            service.config.notif_port)
    try:
        r = session.login(service.config.notif_auth_username,
                          service.config.notif_auth_password)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Notif. "
                             "It might be offline.")
        return

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Notif.")
        return
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Notif. "
                             "(%s)" % session.get_response_message(r))
        return
    
    # create the reminder by talking to notif's oracle
    payload = {
        "title": "🔔",
        "message": msg,
        "send_telegrams": [str(message.chat.id)],
        "trigger_years": [dt.year],
        "trigger_months": [dt.month],
        "trigger_days": [dt.day],
        "trigger_hours": [dt.hour],
        "trigger_minutes": [dt.minute]
    }
    try:
        r = session.post("/reminder/create", payload=payload)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. (%s)" % e)
        return

    # check the reminder-creation response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. "
                             "Notif responded with a %d status code." %
                             r.status_code)
        return
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't create the reminder. (%s)" %
                             session.get_response_message(r))

    # report a success
    trigger_str = dt.strftime("%A, %Y-%m-%d at %I:%M %p")
    service.send_message(message.chat.id,
                         "Success. Triggering on <b>%s</b>." %
                         trigger_str, parse_mode="HTML")

