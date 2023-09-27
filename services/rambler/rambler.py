#!/usr/bin/python3
# This service implements airline price scraping and other routines for
# assisting with planning trips and vacations.

# Imports
import os
import sys
import json
import flask
import time
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Globals
default_trip_year_advance = 4
weekday_strings = [
    "su",
    "mo",
    "tu",
    "we",
    "th",
    "fr",
    "sa"
]
weekday_strings_full = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday"
]
month_strings = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec"
]
month_strings_full = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December"
]

# =============================== Config Class =============================== #
# A sub-config class used to store date/time info for a trip.
class TripTimeConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("number_of_nights",         [list],     required=True),
            ConfigField("departure_weekdays",       [list],     required=True),
            ConfigField("departure_months",         [list],     required=False, default=None),
            ConfigField("departure_years",          [list],     required=False, default=None),
            ConfigField("lookahead_days",           [int],      required=False, default=7)
        ]

    def parse_json(self, jdata: dict):
        super().parse_json(jdata)
        # convert weekday string to numbers, if applicable
        # (sunday = 0, monday = 1, etc.)
        wds = []
        for wd in self.departure_weekdays:
            # process integers
            if type(wd) == int:
                self.check(wd in range(0, 7), "Weekday integer out of range: %d." % wd)
                # append if not already present
                if wd not in wds:
                    wds.append(wd)
            # process weekday strings
            elif type(wd) == str:
                # strip, convert to lowercase, and ensure the string is long
                # enough and a correct weekday string
                d = wd.strip().lower()
                self.check(len(d) >= 2, "Weekday string must be longer than 1 character: \"%s\"." % wd)
                d = d[0:2]
                self.check(d in weekday_strings, "Unknown weekday string: \"%s\"." % wd)
                # append if not already present
                val = weekday_strings.index(d)
                if val not in wds:
                    wds.append(val)
        self.departure_weekdays = sorted(wds)

        # convert month strings to numbers, if applicable
        ms = []
        for m in self.departure_months:
            # process integers
            if type(m) == int:
                self.check(m in range(1, 13), "Month integer out of range: %d." % m)
                # append if not already present
                if m not in ms:
                    ms.append(m)
            # process month strings
            elif type(m) == str:
                # strip/lowercase/reduce and check
                mstr = m.strip().lower()
                self.check(len(mstr) >= 3, "Month string must be longer than 2 characters: \"%s\"." % m)
                mstr = mstr[0:3]
                self.check(mstr in month_strings, "Unknown month string: \"%s\"." % m)
                # append if not already present
                val = month_strings.index(mstr)
                if val not in ms:
                    ms.append(val)
        self.departure_months = sorted(ms)
        
    # Examines the time config's weekdays/months/years/etc. and determines
    # dates that match. A list of datetime.datetime objects are returned in a
    # list of tuples:
    #   
    #   [
    #       [date0_embark, date0_return],
    #       [date1_embark, date1_return],
    #       ...
    #   ]
    #
    # If `count` is specified, only up to the closest `count` dates will be
    # returned.
    # (By default, all dates within the configured `departure_years` are
    # returned. If `departure_years` is not specified, a finite number of years
    # ahead of `after` is examined.)
    #
    # If `after` is specified, only the dates occurring *after* `after` will be
    # returned.
    def get_dates(self, count=None, after=None):
        # Helper function that adds the given number of seconds to a datetime
        # and returns a new version.
        def dt_add(dt: datetime, secs: int):
            return datetime.fromtimestamp(dt.timestamp() + secs)
        
        # handle defaults
        if after is None:
            after = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if count is None:
            count = 1 << 31 # (essentially infinite)

        # if no departure years were given, create our own array
        departure_years = self.departure_years
        if departure_years is None:
            departure_years = [after.year + i for i in range(0, default_trip_year_advance)]

        # iterate starting from the day after the `after` date and iterate
        # forward until matches are found
        result = []
        cd = dt_add(after, 86400)
        while len(result) < count and cd.year in departure_years:
            # if the year doesn't match, advance to the first day of the next
            # year and continue
            if cd.year not in departure_years:
                cd = cd.replace(year=cd.year + 1, month=1, day=1,
                                hour=0, minute=0, second=0, microsecond=0)
                continue

            # if the month doesn't match, advance to the first day of the next
            # month and continue
            if self.departure_months is not None and cd.month not in self.departure_months:
                cd = cd.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                new_month = cd.month + 1 if cd.month < 12 else 1
                new_year = cd.year + 1 if new_month == 1 else cd.year
                cd = cd.replace(month=new_month, year=new_year)
                continue

            # if the weekday doesn't match, advance one day and continue
            if (cd.weekday() + 1) % 7 not in self.departure_weekdays:
                cd = dt_add(cd, 86400)
                continue

            # if we've passed all checks, save this one as the embarking date
            # and compute the return date (do this for ALL trip durations)
            for nights in self.number_of_nights:
                dt_embark = cd.replace(hour=0, minute=0, second=0, microsecond=0)
                dt_return = dt_add(cd, 86400 * nights)
                dt_return = dt_return.replace(hour=0, minute=0, second=0, microsecond=0)
                result.append([dt_embark, dt_return])

            # increase the current date and continue
            cd = dt_add(cd, 86400)

        return result

# A sub-config class used to store flight info for a trip.
class TripFlightConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("airport_embark_depart",    [str],      required=True),
            ConfigField("airport_embark_arrive",    [str],      required=True),
            ConfigField("airport_return_depart",    [str],      required=True),
            ConfigField("airport_return_arrive",    [str],      required=True),
            ConfigField("passengers_adult",         [int],      required=True),
            ConfigField("passengers_child",         [int],      required=False, default=0),
        ]

# A sub-config class used to store all desired configurations to travelling.
class TripConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",                     [str],              required=True),
            ConfigField("timing",                   [TripTimeConfig],   required=True),
            ConfigField("flight",                   [TripFlightConfig], required=False, default=None),
            ConfigField("description",              [str],              required=False, default=None),
        ]

# The official config class for the Rambler service.
class RamblerConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("trips",                    [list],     required=True),
            ConfigField("refresh_rate",             [int],      required=False, default=1800)
        ]

    # Overridden version of parse_json() that utilizes the TripConfig object.
    def parse_json(self, jdata: dict):
        super().parse_json(jdata)
        # parse all objects in the 'trips' list as a TripConfig object.
        trips = []
        for trip in self.trips:
            tc = TripConfig()
            tc.parse_json(trip)
            trips.append(tc)
        self.trips = trips


# ============================== Service Class =============================== #
class RamblerService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = RamblerConfig()
        self.config.parse_file(config_path) 

    # Overridden main function implementation.
    def run(self):
        super().run()
        
        # print a summary of all configured trips
        self.log.write("Initialized with %d trip(s)." % len(self.config.trips))
        for (i, trip) in enumerate(self.config.trips):
            self.log.write(" %2d. \"%s\" - %s" % (i + 1, trip.name, trip.description))
            # build and print a string to show the possible number of days for
            # the trip
            length_str = ""
            number_of_nights_len = len(trip.timing.number_of_nights)
            for (i, val) in enumerate(trip.timing.number_of_nights):
                length_str += "%d" % val
                if i < number_of_nights_len - 2:
                    length_str += ", "
                elif i < number_of_nights_len - 1:
                    length_str += ", or "
            self.log.write("    - Possible lengths: %s nights." % length_str)

            # build and print a string to show the possible weekdays
            weekday_str = ""
            weekdays_len = len(trip.timing.departure_weekdays)
            for (i, wd) in enumerate(trip.timing.departure_weekdays):
                weekday_str += weekday_strings_full[wd]
                if i < weekdays_len - 2:
                    weekday_str += ", "
                elif i < weekdays_len - 1:
                    weekday_str += ", or "
            self.log.write("    - Departure weekdays: %s." % weekday_str)

            # build and print a string to show the possible months
            # TODO

            # log flight information, if given
            if trip.flight is not None:
                self.log.write("    - Embarking: flying from %s, landing in %s." %
                               (trip.flight.airport_embark_depart,
                                trip.flight.airport_embark_arrive))
                self.log.write("    - Returning: flying from %s, landing in %s." %
                               (trip.flight.airport_return_depart,
                                trip.flight.airport_return_arrive))
                                                                                    
        
        # run forever
        while True:
            # iterate through each trip
            for trip in self.config.trips:
                # compute the number of days ahead of *now* to start looking for
                # trip dates
                lookahead = datetime.fromtimestamp(datetime.now().timestamp() +
                                                   (86400 * (trip.timing.lookahead_days - 1)))
                
                # get a listing of all possible dates that match the trip's
                # configuration and iterate through them
                dates = trip.timing.get_dates(after=lookahead)
                #for (dt_embark, dt_return) in dates:
                #    self.log.write("Trip Date: %s --> %s" %
                #                   (dt_embark.strftime("%Y-%m-%d %H:%M:%S %p"),
                #                    dt_return.strftime("%Y-%m-%d %H:%M:%S %p")))

                # if the trip has flight info configured, we'll look for available
                # flights
                if trip.flight is not None:
                    # TODO
                    pass


            # sleep for the configured time
            time.sleep(self.config.refresh_rate)


# ============================== Service Oracle ============================== #
class RamblerOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # TODO
        pass

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=RamblerConfig, service=RamblerService, oracle=RamblerOracle)
cli.run()

