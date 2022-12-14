#!/usr/bin/python3
# A weather service that can forecast any provided location. Makes use of the
# weather.gov API:
# https://www.weather.gov/documentation/services-web-api

# Imports
import os
import sys
import json
import flask

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


# =============================== Config Class =============================== #
class NimbusConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("locations",            [list],     required=True),
            ConfigField("geopy_geocoder",       [str],      required=False,     default="nominatim"),
            ConfigField("geopy_user_agent",     [str],      required=False,     default="private_app_nimbus")
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


# ============================== Service Oracle ============================== #
class NimbusOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint that takes in a location and looks up basic weather
        # statistics.
        @self.server.route("/location/weather", methods=["POST"])
        def endpoint_hello():
            return self.make_response(msg="Hello!")




        # JSON tests endpoint.
        @self.server.route("/json", methods=["GET", "POST"])
        def endpoint_json():
            if not flask.g.jdata:
                return self.make_response(msg="No JSON data provided.")
            # if JSON data was given, send it back
            jmsg = json.dumps(flask.g.jdata, indent=4)
            return self.make_response(msg=jmsg)


    # ------------------------------- Helpers -------------------------------- #
    # Takes in a dictionary with *some* sort of location information and
    # attempts to parse it out and return the appropriate information.
    def parse_location(self, jdata: dict):
        # the user can either pass in a "longitude"/"latitude" pair, or instead
        # pass in an "address" field
        expects = {
            "longitude": float,
            "latitude": float,
            "address": str
        }

        # check for fields in the provided dictionary
        result = {}
        for field in expects:
            if field in jdata:
                # make sure the field is of the correct type if present
                assert type(jdata[field] == expects[field]), \
                       "\"%s\" must be of type %s" % (field, expects[field])
                result[field] = jdata[field]
        
        # make sure either coordinates OR an address was provided
        is_coordinates = "longitude" in result and "latitude" in result
        is_address = "address" in result
        assert is_coordinates or is_address, "longitude/latitude or an address must be given"


# =============================== Runner Code ================================ #
cli = ServiceCLI(service=NimbusService, oracle=NimbusOracle)
cli.run()

