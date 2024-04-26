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
import lib.dtu as dtu


# ================================= Helpers ================================== #
# Returns a weekday number based on the given text. Returns None if the string
# isn't recognized.
def p_weekday(text: str):
    result = dtu.parse_weekday(text)
    if result is None:
        return None
    return result.value + 1

# Converts Python's monday-first weekday encoding to my sunday-first encoding.
def get_weekday(dt: datetime):
    return dtu.get_weekday(dt).value + 1

# Parses the datetime from the user's arguments.
def parse_datetime(args: list):
    # Takes in a value (float or int) and adds/subtracts it from the datetime
    # (by converting to timestamp). The new datetime is returned.
    def adjust_dt(d: datetime, value):
        return datetime.fromtimestamp(d.timestamp() + value)
    
    # start with the current date/time
    dt = datetime.now()
    now = datetime.now()
    
    # iterate through the arguments and look for keywords
    for arg in args:
        same_day = now.year == dt.year and \
                   now.month == dt.month and \
                   now.day == dt.day

        # look for a YYYY-MM-DD date stamp
        datestamp = dtu.parse_yyyymmdd(arg)
        if datestamp is not None:
            # if one was found, reset the datetime to be midnight on the
            # specified day
            dt = datetime(datestamp[0], datestamp[1], datestamp[2],
                          hour=0, minute=0, second=0, microsecond=0)

        # look for mention of a weekday
        wd = p_weekday(arg)
        if wd is not None:
            # increase the current datetime until it lines up with the specified
            # weekday
            dt = adjust_dt(dt, 86400)
            while get_weekday(dt) != wd:
                dt = adjust_dt(dt, 86400)
            continue

        # look for 'am'/'pm'-suffixed times
        clocktime = dtu.parse_time_clock(arg)
        if clocktime is not None:
            h = clocktime[0]
            m = clocktime[1]

            # compute an offset based on the hour and minute (jump to the next
            # day if 'dt' is still set to the current day and the hour/minute
            # have already passed today)
            offset = 0.0
            if dt.hour > h and same_day:
                offset += 86400
            offset += (h - dt.hour) * 3600
            offset += (m - dt.minute) * 60
            dt = adjust_dt(dt, offset)
            continue

        # look for suffixed time offsets ("1d", "2h", "3m")
        offset = dtu.parse_time_offset(arg)
        dt = adjust_dt(dt, offset)
    return dt


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
    first_dot = all_args.index(".") if "." in all_args else len(all_args)
    pieces = [all_args]
    if first_dot >= 0 and len(all_args) >= first_dot + 1:
        pieces = [all_args[:first_dot], all_args[first_dot + 1:]]
    dt_args = pieces[0].split()
    
    # depending on what we found above, set the args to empty, or extract the
    # text appearing after the first "." in the *original* message (*not* the
    # args). Why? So we can preserve newlines and other whitespace elements
    # that were chopped up when the args were formed.
    msg = None
    if len(pieces) >= 2:
        reminder_text_begin = message.text.index(".")
        reminder_text = message.text[reminder_text_begin + 1:].strip()
        msg = reminder_text

    # if the message is in reply to another, we'll use the original message's
    # text as this reminder's message
    is_reply = message.reply_to_message is not None
    if is_reply:
        msg = message.reply_to_message.text

    # if we're missing either group of args, send back an error
    if len(dt_args) == 0:
        msg = "You need to specify some sort of date/time indicator " \
              "<i>before</i> the period in your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return
    if msg is None:
        msg = "You need to specify a message <i>after</i> the period in " \
              "your message."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # parse the arguments as a reminder
    dt = parse_datetime(dt_args)

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
        "title": "" if is_reply else "🔔",
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

