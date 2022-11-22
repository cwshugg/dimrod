#!/usr/bin/python3
# My home lighting service used to toggle and adjust the home's smart lights.

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
from lib.ifttt import Webhook
from lib.cli import ServiceCLI

# Service imports
from light import Light, LightConfig


# =============================== Config Class =============================== #
class LumenConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        # create lumen-specific fields to append to the existing service fields
        fields = [
            ConfigField("lights",           [list],     required=True),
            ConfigField("webhook_event",    [str],      required=True),
            ConfigField("webhook_key",      [str],      required=True)
        ]
        self.fields += fields


# ============================== Service Class =============================== #
class LumenService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = LumenConfig()
        self.config.parse_file(config_path)
        self.webhooker = Webhook(config_path)

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
            light = Light(lconfig.id, lconfig.description,
                          lconfig.has_color, lconfig.has_brightness)
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

        # build JSON data to send to the remote API
        jdata = {"id": light.lid, "action": "on"}

        # make sure color is supported by this light, if color was given
        if color:
            self.check(light.has_color, "\"%s\" does not support color" % light.lid)
            self.check(type(color) == list, "'color' must be a list of 3 RGB ints")
            self.check(len(color) == 3, "'color' must have exactly 3 ints")
            jdata["color"] = "%d,%d,%d" % (color[0], color[1], color[2])
            light.set_color(jdata["color"])

        # do the same for brightness
        if brightness != None:
            self.check(light.has_brightness, "\"%s\" does not support brightness" % light.lid)
            self.check(type(brightness) == float, "'brightness' must be a float between [0.0, 1.0]")
            brightness = max(min(brightness, 1.0), 0.0)
            jdata["brightness"] = brightness
            light.set_brightness(jdata["brightness"])

        # initialize an IFTTT webhook pinger and send the request
        light.set_power(True)
        r = self.webhooker.send(self.config.webhook_event, jdata)
        return r
    
    # Takes in a light ID and turns off the corresponding light.
    def power_off(self, lid):
        light = self.search(lid)
        self.check(light, "unknown light specified: \"%s\"" % lid)

        # build a JSON object and send the request
        jdata = {"id": light.lid, "action": "off"}
        light.set_power(False)
        r = self.webhooker.send(self.config.webhook_event, jdata)
        return r

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
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # iterate through all the lights in the service and build a JSON
            # list to return
            lights = []
            for light in self.service.lights:
                lights.append(light.to_json())
            # send back the list
            return self.make_response(success=True, payload=lights)
        
        # Endpoint used to toggle a single light.
        @self.server.route("/toggle", methods=["POST"])
        def endpoint_toggle():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # make sure some sort of data was passed
            if not flask.g.jdata:
                return self.make_response(msg="Missing/invalid toggle information.",
                                          success=False, rstatus=400)

            # otherwise, parse the data to understand the request
            jdata = flask.g.jdata
            if "id" not in jdata:
                return self.make_response(msg="Request must contain a light ID.",
                                          success=False, rstatus=400)
            if "action" not in jdata:
                return self.make_response(msg="Request must contain an action.",
                                          success=False, rstatus=400)

            lid = jdata["id"]
            action = jdata["action"].lower()
            color = None
            brightness = None
            
            # look for the optional 'color' field. It must come as a string of
            # three RGB integers, separated by commas. (ex: "125,13,0")
            if "color" in jdata:
                try:
                    color = jdata["color"].strip().split(",")
                    assert len(color) == 3
                    for (i, cstr) in enumerate(color):
                        color[i] = int(cstr.strip())
                except:
                    return self.make_response(msg="Invalid color format",
                                              success=False, rstatus=400)
            
            # look for the optional 'brightness' field. It must come as a float
            # between 0.0 and 1.0
            if "brightness" in jdata:
                try:
                    brightness = jdata["brightness"]
                    assert type(brightness) in [float, int]
                    brightness = float(brightness)
                    assert brightness >= 0.0 and brightness <= 1.0
                except:
                    return self.make_response(msg="Invalid brightness value.",
                                              success=False, rstatus=400)

            # invoke the service's API according to the given action
            try:
                r = None
                if action == "on":
                    r = self.service.power_on(lid, color=color, brightness=brightness)
                elif action == "off":
                    r = self.service.power_off(lid)
                else:
                    return self.make_response(msg="Invalid action.",
                                              success=False, rstatus=400)

                # based on the response from IFTTT, construct an appropriate
                # response message
                status_code = self.service.webhooker.get_status_code(r)
                success = status_code == 200
                message = None
                if not success:
                    message = "IFTTT returned %d." % status_code
                    errors = self.service.webhooker.get_errors(r)
                    for e in errors:
                        message += "\n%s" % e
                return self.make_response(success=success, msg=message)
            except Exception as e:
                return self.make_response(msg=str(e), success=False)


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=LumenConfig, service=LumenService, oracle=LumenOracle)
cli.run()

