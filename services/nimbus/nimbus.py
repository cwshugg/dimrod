#!/usr/bin/python3
# A weather service that can forecast any provided location. Makes use of the
# weather.gov API:
# https://www.weather.gov/documentation/services-web-api

# Imports
import os
import sys
import json
import flask
import requests
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Nimbus imports
from location import Location
from forecast import Forecast


# =============================== Config Class =============================== #
class NimbusConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("locations",            [list],     required=True),
            ConfigField("geopy_geocoder",       [str],      required=False,     default="nominatim"),
            ConfigField("geopy_user_agent",     [str],      required=False,     default="private_app_nimbus"),
            ConfigField("api_address",          [str],      required=False,     default="api.weather.gov"),
            ConfigField("api_user_agent",       [str],      required=False,     default="private_app_nimbus")
        ]


# ============================== Service Class =============================== #
class NimbusService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = NimbusConfig()
        self.config.parse_file(config_path)

        # parse the 'locations' as an array of Location objects
        locs = []
        for entry in self.config.locations:
            # parse the JSON data as a location
            loc = Location()
            loc.parse_json(entry)

            # geolocate the location depending on what was given
            result = loc.locate()
            if result is None:
                self.log.write("Failed to geolocate: %s" % loc)
            else:
                self.log.write("Successfully geolocated: %s" % loc)
            locs.append(loc)
        self.locations = locs

    # Main runner function.
    def run(self):
        super().run()

    # ------------------------------ Interface ------------------------------- #
    # Accepts a Location object and attempts to look up its weather status.
    # Returns a Forecast object, or None if a forecast according to the 'when'
    # time can't be found.
    def forecast(self, location: Location, when: datetime):
        # make sure we have a longitude and latitude for the location
        if location.longitude is None or location.latitude is None:
            location.locate()

        # look up the correct URLs and parameters for the location via the API
        url = "https://%s/points/%.4f,%.4f" % (self.config.api_address,
                                               location.latitude,
                                               location.longitude)
        hdrs = {"User-Agent": self.config.api_user_agent}
        r = requests.get(url, headers=hdrs)
        rdata = r.json()

        # now that we have the apporpriate information for the given location,
        # extract the correct URL to ping next for forecast information
        properties = rdata["properties"]
        url = properties["forecast"]
        r = requests.get(url, headers=hdrs)
        rdata = r.json()

        periods = rdata["properties"]["periods"]
        for pdata in periods:
            fc = Forecast()
            fc.parse_json(pdata)

            # compare the datetimes and determine if the requested time has a
            # matching forecast
            wts = when.timestamp()
            if wts >= fc.time_start.timestamp() and \
               wts <= fc.time_end.timestamp():
                return fc
        return None


# ============================== Service Oracle ============================== #
class NimbusOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # Endpoint that takes in a location and looks up basic weather
        # statistics.
        @self.server.route("/weather/bylocation", methods=["POST"])
        def endpoint_weather_bylocation():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="No JSON data provided.")

            # parse a location from the request payload
            location = Location()
            location.parse_json(flask.g.jdata)

            # if a "when" field is defined in the JSON data, interpret it as
            # a timestamp (in seconds)
            when = datetime.now()
            if "when" in flask.g.jdata:
                if type(flask.g.jdata["when"]) not in [int, float]:
                    return self.make_response(msg="The \"when\" value must be in seconds.")
                when = datetime.fromtimestamp(flask.g.jdata["when"])

            # next, look up the location's current weather
            fc = self.service.forecast(location, when)
            if fc is None:
                return self.make_response(success=False, msg="No forecast for that time exists.")
            return self.make_response(payload=fc.to_json())

        # Endpoint that takes in saved location's name and looks up basic
        # weather statistics for it.
        @self.server.route("/weather/byname", methods=["POST"])
        def endpoint_weather_byname():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="No JSON data provided.")

            # parse a location from the request payload and use geopy to look
            # it up
            if "name" not in flask.g.jdata:
                return self.make_response(success=False, msg="No \"name\" field provided.")
            name = flask.g.jdata["name"]

            # try to match a name to a location
            location = None
            for l in self.service.locations:
                if l.name == name:
                    location = l
                    break

            # if we couldn't find a location, stop here
            if location is None:
                return self.make_response(success=False,
                                          msg="No known location by name \"%s\"" % name)

            # if a "when" field is defined in the JSON data, interpret it as
            # a timestamp (in seconds)
            when = datetime.now()
            if "when" in flask.g.jdata:
                if type(flask.g.jdata["when"]) not in [int, float]:
                    return self.make_response(msg="The \"when\" value must be in seconds.")
                when = datetime.fromtimestamp(flask.g.jdata["when"])

            # next, look up the location's current weather
            fc = self.service.forecast(location, when)
            if fc is None:
                return self.make_response(success=False, msg="No forecast for that time exists.")
            return self.make_response(payload=fc.to_json())


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=NimbusConfig, service=NimbusService, oracle=NimbusOracle)
    cli.run()

