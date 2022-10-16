#!/usr/bin/python3
# Might home lighting service used to toggle and adjust the home's smart lights.

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

# Service imports
from light import Light, LightConfig


# =============================== Config Class =============================== #
class LumenConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        # create lumen-specific fields to append to the existing service fields
        fields = [
            ConfigField("lights",        [list],     required=True)
        ]
        self.fields += fields


# ============================== Service Class =============================== #
class LumenService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = LumenConfig()
        self.config.parse_file(config_path)

        # for each of the entries in the config's 'lights' field, we'll create a
        # new Light object
        self.lights = []
        for ldata in self.config.lights:
            # create a LightConfig object, parse from the sub-JSON, then make
            # sure the given light ID doesn't already exist
            lconfig = LightConfig()
            lconfig.parse_json(ldata)
            self.check(self.search(lconfig.id) == None,
                       "light \"%s\" is defined more than once" % lconfig.id)

            # if we're good, initialize a new Light object and append it to our
            # list of lights
            light = Light(lconfig.id, lconfig.has_color, lconfig.has_brightness)
            self.lights.append(light)
            self.log.write("loaded light: %s" % light)
    
    # Overridden main function implementation.
    def run(self):
        super().run()
        # TODO - after implementing oracle-based light updates (so we can turn
        # lights on and off through the home server website), implement the
        # ability to schedule light events within the config file

    # ------------------------------ Lumen API ------------------------------- #
    # Takes in a light ID, and optional color and brightness parameters, and
    # attempts to turn the corresponding light on.
    #   - 'color' must be an array of 3 RGB integers
    #   - 'brightness' must be a float between 0.0 and 1.0 (inclusive)
    def power_on(self, lid, color=None, brightness=None):
        light = self.search(lid)
        self.check(light, "unknown light specified: \"%s\"" % lid)

        # TODO
    
    # Takes in a light ID and turns off the corresponding light.
    def power_off(self, lid):
        light = self.search(lid)
        self.check(light, "unknown light specified: \"%s\"" % lid)

        # TODO

    # ------------------------------- Helpers -------------------------------- #
    # Searches lumen's light array and returns a Light object if one with a
    # maching light ID is found. Otherwise, None is returned.
    def search(self, lid):
        for light in self.lights:
            if light.lid == lid:
                return light
        return None
    
    # Custom assertion function.
    def check(self, condition, msg):
        if not condition:
            raise Exception("Lumen Error: %s" % msg)


# ============================== Service Oracle ============================== #
class LumenOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint that retrieves information about which lights are available
        # for pinging.
        @self.server.route("/lights")
        def endpoint_lights():
            return self.make_response(msg="TODO - LIGHT LIST")
        
        # Endpoint used to toggle a single light.
        @self.server.route("/toggle", methods=["POST"])
        def endpoint_toggle():
            # make sure some sort of data was passed
            if not flask.g.jdata:
                return self.make_response(msg="No toggle information provided.",
                                          success=False, rstatus=400)

            # otherwise, parse the data to understand the request
            jdata = flask.g.jdata
            return self.make_response(msg="TODO - TOGGLE")


# =============================== Runner Code ================================ #
cp = os.path.join(os.path.dirname(__file__), "lumen.json")
ls = LumenService(cp)
lo = LumenOracle(cp, ls)
ls.start()
lo.start()
lo.join()
ls.join()

