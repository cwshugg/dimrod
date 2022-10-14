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
from lib.service import Service
from lib.oracle import Oracle


# ============================== Service Class =============================== #
class LumenService(Service):
    # Constructor.
    def __init__(self):
        config_path = os.path.join(os.path.dirname(__file__), "lumen.json")
        super().__init__(os.path.join(config_path))
        self.lights = []
    
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
    def turn_on(self, lid, color=None, brightness=None):
        light = self.search(lid)
        assert light, "unknown light specified: \"%s\"" % lid

        # TODO
    
    # Takes in a light ID and turns off the corresponding light.
    def turn_off(self, lid):
        light = self.search(lid)
        assert light, "unknown light specified: \"%s\"" % lid

        # TODO

    # ------------------------------- Helpers -------------------------------- #
    # Searches lumen's light array and returns a Light object if one with a
    # maching light ID is found. Otherwise, None is returned.
    def search(self, lid):
        for light in self.lights:
            if light.lid == lid:
                return light
        return None


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
                                          rstatus=400)

            # otherwise, parse the data to understand the request
            jdata = flask.g.jdata
            return self.make_response(msg="TODO - TOGGLE")


# =============================== Runner Code ================================ #
ls = LumenService()
lo = LumenOracle(ls)
ls.start()
lo.start()
lo.join()
ls.join()

