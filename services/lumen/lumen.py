#!/usr/bin/python3
# My home lighting service used to toggle and adjust the home's smart lights.

# Imports
import os
import sys
import json
import time
import requests
import flask
from datetime import datetime
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.ifttt import WebhookConfig, Webhook
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
            ConfigField("lights",               [list],     required=True),
            ConfigField("webhook_event",        [str],      required=True),
            ConfigField("webhook_key",          [str],      required=True),
            ConfigField("refresh_rate",         [int],      required=False,     default=60),
            ConfigField("action_threads",       [int],      required=False,     default=4),
        ]
        self.fields += fields


# ================================ Threading ================================= #
# A simple class used to represent a single action to be carried out by Lumen
# threads.
class LumenThreadQueueAction:
    # Constructor.
    def __init__(self, action: str, lid: str, color=None, brightness=None):
        self.action = action.strip().lower()
        self.lid = lid
        self.color = color
        self.brightness = brightness
    
# Represents a queue used to submit actions to lumen threads.
class LumenThreadQueue:
    # Constructor.
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)
        self.queue = []

    # Pushes to the queue and alerts a waiting thread.
    def push(self, action: LumenThreadQueueAction):
        self.lock.acquire()
        self.queue.append(action)
        self.cond.notify()
        self.lock.release()
    
    # Pops from the queue, blocking if the queue is empty.
    def pop(self):
        self.lock.acquire()
        while len(self.queue) == 0:
            self.cond.wait()
        action = self.queue.pop(0)
        self.lock.release()
        return action

# Represents an individual thread used to handle lumen requests. Because the
# activation of lights/devices may have some noticeable latency, these threads
# provide a way to parallelize things.
class LumenThread(threading.Thread):
    # Constructor
    def __init__(self, service, queue: LumenThreadQueue):
        super().__init__(target=self.run)
        self.service = service
        self.queue = queue
    
    # Writes a log message using the lumen service's log object.
    def log(self, msg: str):
        ct = threading.current_thread()
        self.service.log.write("[Action Thread %d] %s" % (ct.native_id, msg))
    
    # The thread's main function.
    def run(self):
        self.log("Spawned.")

        # loop forever
        while True:
            # pop from the queue (this will block if the queue is empty)
            action = self.queue.pop()

            # process the action
            if action.action == "on":
                self.log("Found queued ON action for ID \"%s\"." % action.lid)
                # run the service' power_on function with the action's params
                self.service.power_on(action.lid,
                                      color=action.color,
                                      brightness=action.brightness)
            elif action.action == "off":
                self.log("Found queued OFF action for ID \"%s\"." % action.lid)
                # run the service' power_off function with the action's params
                self.service.power_off(action.lid)
            else:
                self.log("Found unknown action: \"%s\"." % action.action)


# ================================= Service ================================== #
# The main Lumen service class.
class LumenService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = LumenConfig()
        self.config.parse_file(config_path)
        
        # set up IFTTT webhook object
        webhook_conf = WebhookConfig()
        webhook_conf.parse_file(config_path)
        self.webhooker = Webhook(webhook_conf)

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
            light = Light(lconfig)
            self.lights.append(light)
            self.log.write("loaded light: %s" % light)

        # set up a queue and threads for asynchronous lumen processing (make
        # sure at least one processing thread is specified)
        self.check(self.config.action_threads > 0,
                   "at least one action thread (action_threads) must be specified.")
        self.queue = LumenThreadQueue()
        self.threads = []
        # create and spawn the specified number of threads
        for i in range(self.config.action_threads):
            t = LumenThread(self, self.queue)
            t.start()
            self.threads.append(t)

    # Overridden main function implementation.
    def run(self):
        super().run()

    # ------------------------------ Lumen API ------------------------------- #
    # Takes in a light ID, and optional color and brightness parameters, and
    # attempts to turn the corresponding light on.
    #   - 'color' must be an array of 3 RGB integers
    #   - 'brightness' must be a float between 0.0 and 1.0 (inclusive)
    def power_on(self, lid, color=None, brightness=None):
        light = self.search(lid)
        self.check(light, "unknown light specified: \"%s\"" % lid)
        
        # acquire the light's lock, in case another thread is trying to access
        # the same light
        light.lock()

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
        light.unlock() # release the light's lock
        return r
    
    # Adds a power_on action to the thread queue for asynchronous processing.
    def queue_power_on(self, lid, color=None, brightness=None):
        a = LumenThreadQueueAction("on", lid, color=color, brightness=brightness)
        self.log.write("Queueing ON action for %s." % lid)
        self.queue.push(a)
    
    # Takes in a light ID and turns off the corresponding light.
    def power_off(self, lid):
        light = self.search(lid)
        self.check(light, "unknown light specified: \"%s\"" % lid)

        # acquire the light's lock, in case another thread is trying to access
        # the same light
        light.lock()

        # build a JSON object and send the request
        jdata = {"id": light.lid, "action": "off"}
        light.set_power(False)
        r = self.webhooker.send(self.config.webhook_event, jdata)
        light.unlock() # release the light's lock
        return r
    
    # Adds a power_off action to the thread queue for asynchronous processing.
    def queue_power_off(self, lid):
        a = LumenThreadQueueAction("off", lid)
        self.log.write("Queueing OFF action for %s." % lid)
        self.queue.push(a)

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
                if action == "on":
                    self.service.queue_power_on(lid, color=color, brightness=brightness)
                elif action == "off":
                    self.service.queue_power_off(lid)
                else:
                    return self.make_response(msg="Invalid action.",
                                              success=False, rstatus=400)
                
                # because we asynchronously queued the action, we can't wait for
                # it to finish and retrieve the response (otherwise that would
                # defeat the purpose). So, simply return a success message
                return self.make_response(success=True, msg="Action queued successfully.")
            except Exception as e:
                return self.make_response(msg=str(e), success=False)


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=LumenConfig, service=LumenService, oracle=LumenOracle)
cli.run()

