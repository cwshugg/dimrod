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
import lib.dtu as dtu
from lib.oracle import OracleSession
from lib.google.google_calendar import GoogleCalendar, GoogleCalendarConfig


# ================================= Helpers ================================== #
# Creates and returns a GoogleCalendar instance to use for interacting with the
# calendar.
def get_google_calendar(service):
    conf = service.config.google_calendar_config
    gc = GoogleCalendar(conf)
    return gc


# =================================== Main =================================== #
def subcommand_list_events(service, message, args: list,
                           dt_start: datetime, dt_end: datetime):
    # get a google calendar instance and retrieve all events
    gc = get_google_calendar(service)
    events = gc.get_events_between(service.config.google_calendar_id,
                                   dt_start, dt_end)

    # form the intro sentence of the message
    events_len = len(events)
    msg = "There are %s event%s between <b>%s</b> and <b>%s</b>." % \
          ("no" if events_len == 0 else events_len,
           "s" if events_len != 1 else "",
           dt_start.strftime("%Y-%m-%d at %I:%M %p"),
           dt_end.strftime("%Y-%m-%d at %I:%M %p"))

    # quit early if no events were found
    if len(events) == 0:
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # iterate through each event and create a long message
    msg += "\n\n"
    for event in events:
        # extract and parse the starting and ending times
        event_start = GoogleCalendar.get_event_start(event)
        event_end = GoogleCalendar.get_event_end(event)

        # form a "when" formatted datetime message
        when = ""
        if dtu.has_same_exact_day(event_start, event_end):
            when = "%s, from %s to %s" % \
                   (event_start.strftime("%A, %b %d"),
                    event_start.strftime("%I:%M %p"),
                    event_end.strftime("%I:%M %p"))
        else:
            when = "%s to %s" % \
                   (event_start.strftime("%A, %Y-%m-%d at %I:%M %p"),
                   event_end.strftime("%A, %Y-%m-%d at %I:%M %p"))

        # form the full message
        msg += "<b>%s</b>\n" \
               "• <u>When</u>: %s\n" % \
               (GoogleCalendar.get_event_title(event), when)
        desc = GoogleCalendar.get_event_description(event)
        if desc is not None:
            msg += "• <u>Description</u>: %s\n" % desc
        msg += "\n"
        print(event)

    service.send_message(message.chat.id, msg, parse_mode="HTML")

def subcommand_create_event(service, message, args: list,
                            dt_start: datetime, dt_end: datetime,
                            title: str, description=None):
    # get a google calendar instance and create the event
    gc = get_google_calendar(service)
    gc.create_event(service.config.google_calendar_id,
                    dt_start,
                    dt_end,
                    title,
                    description=description,
                    time_zone=service.config.google_calendar_timezone)
    
    # write a message to confirm the event's creation
    msg = "Success. Event created:\n\n"
    msg += "<b>%s</b>\n" % title
    if description is not None:
        msg += "%s\n\n" % description
    else:
        msg += "\n"
    msg += "• Starts: %s\n" % dt_start.strftime("%A, %Y-%m-%d at %I:%M %p")
    msg += "• Ends: %s\n" % dt_end.strftime("%A, %Y-%m-%d at %I:%M %p")
    service.send_message(message.chat.id, msg, parse_mode="HTML")

def command_calendar(service, message, args: list):
    if len(args) < 2:
        # by default, list all events for the next 24 hours
        dt_start = datetime.now()
        dt_end = dtu.add_days(dt_start, 1)
        subcommand_list_events(service, message, args, dt_start, dt_end)
        return

    # join all the arguments together, then split them by periods
    all_args = " ".join(args[1:])
    psplits = all_args.split(".")

    # iterate the arguments and grab what information was provided
    event_start = None
    event_end = None
    event_title = None
    event_description = None
    for arg in psplits:
        arg = arg.strip()
        
        # attempt to parse a datetime
        parse_datetime_now = event_start if event_start is not None else None
        dt = dtu.parse_datetime(arg.split(), now=parse_datetime_now)

        # set the event start and end depending on what already exists
        if dt is not None:
            if event_start is None:
                event_start = dt
            elif event_end is None:
                event_end = dt
            continue

        # if parsing that fails, interpret the first string as the title
        if event_title is None:
            event_title = arg
        elif event_description is None:
            event_description = arg
        continue
    
    # make sure at least a starting time was specified
    if event_start is None:
        msg = "You must specify a starting datetime " \
              "(and optionally, an ending datetime). For example:\n\n" \
              "<code>/calendar Friday 8am. 8:45am. Doctor's Appointment</code>\n" \
              "<code>/calendar 2030-07-05 1pm. Work Lunch</code>\n"
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # if an ending time wasn't specified, default it to ending one hour after
    # the starting time
    if event_end is None:
        event_end = dtu.add_hours(event_start, 1)

    # if an event title wasn't specified, then only dates must have been
    # specified. What we'll do in this case is list all events occurring
    # between the starting and ending time
    if event_title is None:
        subcommand_list_events(service, message, args, event_start, event_end)
        return

    # create the event
    subcommand_create_event(service, message, args,
                            event_start, event_end,
                            event_title, description=event_description)

