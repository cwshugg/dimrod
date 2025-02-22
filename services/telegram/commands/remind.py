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
    dt = dtu.parse_datetime(dt_args)
    if dt is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't parse a date or time from your message.")
        return

    # create a HTTP session with notif
    session = OracleSession(service.config.notif)
    try:
        r = session.login()
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

