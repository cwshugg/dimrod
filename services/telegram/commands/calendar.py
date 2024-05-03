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

        # is the event starting/ending today or tomorrow?
        now = datetime.now()
        tomorrow = dtu.add_days(now, 1)
        event_starts_today = dtu.has_same_exact_day(event_start, now)
        event_starts_tomorrow = dtu.has_same_exact_day(event_start, tomorrow)
        event_ends_tomorrow = dtu.has_same_exact_day(event_end, tomorrow)
        
        # is the event all day?
        event_is_all_day = dtu.diff_in_hours(event_end, event_start) == 24 and \
                           dtu.is_exact_midnight(event_start)

        # select a string to specify the day the event starts
        day_str_start = None
        if event_starts_today:
            day_str_start = "Today"
        elif event_starts_tomorrow:
            day_str_start = "Tomorrow"
        else:
            day_str_start = event_start.strftime("%A, %b %d")

        # do the same for the day the event ends
        day_str_end = None
        if event_ends_tomorrow:
            day_str_end = "Tomorrow"
        else:
            day_str_end = event_end.strftime("%A, %b %d")

        # form a "when" formatted datetime message
        when = ""
        if dtu.has_same_exact_day(event_start, event_end):
            when = "%s, from %s to %s" % \
                   (day_str_start,
                   event_start.strftime("%I:%M %p"),
                   event_end.strftime("%I:%M %p"))
        else:
            if event_is_all_day:
                when = "%s, all day" % day_str_start
            else:
                when = "%s at %s to %s at %s" % \
                       (day_str_start,
                        event_start.strftime("%I:%M %p"),
                        day_str_end,
                        event_end.strftime("%I:%M %p"))

        # form the full message
        msg += "<b>%s</b>\n" \
               "• <u>When</u>: %s\n" % \
               (GoogleCalendar.get_event_title(event), when)
        desc = GoogleCalendar.get_event_description(event)
        if desc is not None:
            msg += "• <u>Description</u>: %s\n" % desc
        msg += "\n"

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

    # if an event title wasn't specified, then only dates must have been
    # specified. What we'll do in this case is list all events occurring
    # between the starting and ending time
    if event_title is None:
        # if an ending time wasn't specified, default to 24 hours after the
        # starting time. This'll let the user see all the events coming up over
        # the next 24 hours after the start time
        if event_end is None:
            event_end = dtu.add_days(event_start, 1)
        subcommand_list_events(service, message, args, event_start, event_end)
        return

    # if an ending time wasn't specified, default it to ending one hour after
    # the starting time. This way, calendar events that are created will
    # default to being 1 hour in length.
    if event_end is None:
        event_end = dtu.add_hours(event_start, 1)

    # create the event
    subcommand_create_event(service, message, args,
                            event_start, event_end,
                            event_title, description=event_description)

