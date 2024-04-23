# This module implements an interface around the Google Calendar API, to
# provide a way for DImROD to interact with my calendars.
#
# https://developers.google.com/calendar/api/guides/overview

# Imports
import os
import sys
from datetime import datetime
import pytz
from google.google_auth import GoogleCredentials
from googleapiclient.discovery import build

# Enable imports from the grandparent directory
gpdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if gpdir not in sys.path:
    sys.path.append(gpdir)

# Imports
from lib.config import Config, ConfigField

# An object representing configured inputs for a GoogleCalendar object.
class GoogleCalendarConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("service_account_credentials_path",  [str],  required=True),
            lib.config.ConfigField("service_account_scopes",            [list], required=False, default=["https://www.googleapis.com/auth/calendar"]),
        ]

# The main Google Calendar API object.
class GoogleCalendar:
    # Constructor. Takes in a path to the Google Credential Service Account
    # file to use for authentication.
    def __init__(self, config: GoogleCalendarConfig):
        self.config = config
        self.creds = GoogleCredentials(self.config.service_account_scopes,
                                       self.config.service_account_credentials_path)

        # authenticate with the service account and use it to build the service
        c = self.creds.authenticate()
        self.service = build("calendar", "v3", credentials=c)
    
    # Takes in a datetime (or gets the current time) and returns it as a
    # Google-API-friendly UTC time string.
    def make_calendar_time(self, dt=None, time_zone=None):
        # if no datetime was given, use the current time
        if dt is None:
            dt = datetime.now(timezone.utc).astimezone()

        # convert the given timezone name (if one was given) into a Python
        # tzinfo object, and convert the given datetime to use this timezone
        suffix = ""
        if time_zone is not None:
            tz = pytz.timezone(time_zone)
            dt = dt.astimezone(tz)
        else:
            suffix = "Z"
        
        # convert to ISO format and return
        return dt.isoformat() + suffix
    
    # --------------------------- Event Retrieval ---------------------------- #
    # Generic event-retrieving function.
    def get_events(self, calid: str,
                   dt_start=None,
                   dt_end=None,
                   count=None,
                   single_events=True,
                   order_by="startTime"):
        # create a list of arguments to pass into the API query
        args = {
            "calendarId": calid,
            "timeMin": self.make_calendar_time(dt=dt_start),
            "singleEvents": single_events,
            "orderBy": order_by
        }
        if count is not None:
            args["maxResults"] = count
        if dt_end is not None:
            args["timeMax"] = self.make_calendar_time(dt=dt_end)
            assert dt_start.timestamp() < dt_end.timestamp(), \
                   "The ending datetime is not greater than the starting datetime."
        
        # execute the query and retrieve the results
        result = self.service.events().list(**args).execute()
        events = result.get("items", [])
        
        # return an empty list, or the list of retrieved events
        if events is None:
            return []
        return events

    # Gets the next `count` events occurring after `dt` (which is the current
    # time by default). The calendar referenced by `calid` is searched.
    def get_events_after(self, calid: str, count=10, dt=None):
        return self.get_events(calid, count=count, dt_start=dt)

    # Returns all events between the given two datetime objects.
    def get_events_between(self, calid: str, dt_start: datetime, dt_end: datetime):
        return self.get_events(calid, dt_start=dt_start, dt_end=dt_end)

    # ---------------------------- Event Creation ---------------------------- #
    # Creates an event, given the starting and ending datetimes, the title, and
    # other optional parameters.
    def create_event(self, calid: str,
                     dt_start: datetime,
                     dt_end: datetime,
                     title: str,
                     time_zone=None,
                     description=None,
                     location=None):
        # create an event JSON object with the given parameters
        event = {
            "summary": title,
            "start": {
                "dateTime": self.make_calendar_time(dt_start, time_zone)
            },
            "end": {
                "dateTime": self.make_calendar_time(dt_end, time_zone)
            },
        }
        if time_zone is not None:
            event["start"]["timeZone"] = str(time_zone)
            event["end"]["timeZone"] = str(time_zone)
        if description is not None:
            event["description"] = str(description)
        if location is not None:
            event["location"] = str(location)

        # pass the event to the API
        return self.service.events().insert(calendarId=calid, body=event).execute()

