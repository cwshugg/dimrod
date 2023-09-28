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

# Rambler imports
from configs import *
from flightscrape import *

# Globals
flight_scrapers = {
    "kayak": FlightScraper_Kayak
}


# =============================== Config Class =============================== #
# The official config class for the Rambler service.
class RamblerConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("trips",                    [list],     required=True),
            ConfigField("flight_scraper",           [str],      required=False, default="kayak"),
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

        # verify the flight scraper
        self.flight_scraper = self.flight_scraper.strip().lower()
        self.check(self.flight_scraper in flight_scrapers,
                   "Unknown flight scraper: \"%s\"." % self.flight_scraper)


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
                if i < number_of_nights_len - 1:
                    length_str += ", "
                    length_str += "" if i < number_of_nights_len - 2 else "or "
            if number_of_nights_len > 0:
                self.log.write("    - Possible lengths: %s nights." % length_str)

            # build and print a string to show the possible weekdays
            weekday_str = ""
            weekdays_len = 0
            if trip.timing.departure_weekdays is not None:
                weekdays_len = len(trip.timing.departure_weekdays)
                for (i, wd) in enumerate(trip.timing.departure_weekdays):
                    weekday_str += weekday_strings_full[wd]
                    if i < weekdays_len - 1:
                        weekday_str += ", "
                        weekday_str += "" if i < weekdays_len - 2 else "or "
                if weekdays_len > 0:
                    self.log.write("    - Departure weekdays: %s." % weekday_str)

            # build and print a string to show the possible months
            month_str = ""
            months_len = 0
            if trip.timing.departure_months is not None:
                months_len = len(trip.timing.departure_months)
                for (i, m) in enumerate(trip.timing.departure_months):
                    month_str += month_strings_full[m]
                    if i < months_len - 1:
                        month_str += ", "
                        month_str += "" if i < months_len - 2 else "or "
                if months_len > 0:
                    self.log.write("    - Departure months: %s." % month_str)

            # build and print a string to show the possible years
            year_str = ""
            years_len = 0
            if trip.timing.departure_years is not None:
                years_len = len(trip.timing.departure_years)
                for (i, y) in enumerate(trip.timing.departure_years):
                    year_str += "%d" % y
                    if i < years_len - 1:
                        year_str += ", "
                        year_str += "" if i < years_len - 2 else "or "
                if years_len > 0:
                    self.log.write("    - Departure years: %s." % year_str)

            # log flight information, if given
            if trip.flight is not None:
                self.log.write("    - Embarking: flying from %s, landing in %s." %
                               (trip.flight.airport_embark_depart,
                                trip.flight.airport_embark_arrive))
                self.log.write("    - Returning: flying from %s, landing in %s." %
                               (trip.flight.airport_return_depart,
                                trip.flight.airport_return_arrive))
            
                # log the number of people going on the trip
                people_str = "%d adult%s" % (trip.flight.passengers_adult,
                                             "" if trip.flight.passengers_adult == 1 else "s")
                if trip.flight.passengers_child > 0:
                    people_str += " and %d child%s" % \
                                  (trip.flight.passengers_child,
                                   "" if trip.flight.passengers_child == 1 else "ren")
                self.log.write("    - Need airline tickets for: %s." % people_str)
        
        # initialize a flight scraper
        fscraper = flight_scrapers[self.config.flight_scraper]()

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
                    result = fscraper.scrape(trip.flight, trip.timing)
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

