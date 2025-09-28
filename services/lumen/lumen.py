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
from lib.oracle import Oracle, OracleSessionConfig
from lib.nla import NLAEndpoint, NLAEndpointHandlerFunction, NLAResult, NLAEndpointInvokeParameters
from lib.ifttt import WebhookConfig, Webhook
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface
from lib.wyze import WyzeConfig, Wyze
from lib.lifx import LIFXConfig, LIFX

# Service imports
from light import Light, LightConfig


# =============================== Config Class =============================== #
class LumenConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        # create lumen-specific fields to append to the existing service fields
        fields = [
            ConfigField("lights",               [list],         required=True),
            ConfigField("webhook_event",        [str],          required=True),
            ConfigField("webhook_key",          [str],          required=True),
            ConfigField("wyze_config",          [WyzeConfig],   required=True),
            ConfigField("dialogue",             [DialogueConfig], required=True),
            ConfigField("lifx_config",          [LIFXConfig],   required=False, default=None),
            ConfigField("refresh_rate",         [int],          required=False, default=60),
            ConfigField("action_threads",       [int],          required=False, default=8),
            ConfigField("nla_toggle_dialogue_retries", [int],   required=False, default=4),
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
                try:
                    # run the service' power_on function with the action's params
                    self.service.power_on(action.lid,
                                          color=action.color,
                                          brightness=action.brightness)
                except Exception as e:
                    self.log("Failed to turn device \"%s\" on: %s" % (action.lid, e))
            elif action.action == "off":
                self.log("Found queued OFF action for ID \"%s\"." % action.lid)
                try:
                    # run the service' power_off function with the action's params
                    self.service.power_off(action.lid)
                except Exception as e:
                    self.log("Failed to turn device \"%s\" off: %s" % (action.lid, e))
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

        # set up a Wyze API object
        self.wyze = Wyze(self.config.wyze_config)
        try:
            self.log.write("Attempting to log into Wyze API...")
            self.wyze.login()
            self.log.write("Logged into Wyze successfully.")
        except Exception as e:
            self.log.write("Failed to log into Wyze API: %s" % e)

        # set up a LIFX LAN object
        lifx_config = self.config.lifx_config
        if lifx_config is None:
            lifx_config = LIFXConfig()
            lifx_config.parse_json({})
        self.lifx = LIFX(lifx_config)

        # invoke the LIFX's `get_lights()` function to initially discover as
        # many lights as possible. This way, we don't have a delay when the
        # first LIFX light toggle request comes along
        try:
            self.log.write("Attempting to initially discover LIFX lights...")
            lifx_lights = self.lifx.get_lights()
            lifx_lights_len = len(lifx_lights)
            if lifx_lights_len > 0:
                self.log.write("Initially discovered %d LIFX lights." % lifx_lights_len)
            else:
                self.log.write("Initially discovered no LIFX lights.")
        except Exception as e:
            self.log.write("Failed to initially discover LIFX lights: %s" % e)

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
            self.log.write("Loaded light: %s" % light)

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

        # make sure color is supported by this light, if color was given
        if color is not None:
            self.check(light.has_color, "\"%s\" does not support color" % light.lid)
            self.check(type(color) == list, "'color' must be a list of 3 RGB ints")
            self.check(len(color) == 3, "'color' must have exactly 3 ints")
            light.set_color(color)

        # do the same for brightness
        if brightness is not None:
            self.check(light.has_brightness, "\"%s\" does not support brightness" % light.lid)
            self.check(type(brightness) == float, "'brightness' must be a float between [0.0, 1.0]")
            brightness = max(min(brightness, 1.0), 0.0)
            light.set_brightness(brightness)

        # choose a way to toggle the light
        r = None
        if light.match_tags("wyze"):
            r = self.toggle_wyze(light, "on", color=color, brightness=brightness)
        elif light.match_tags("lifx"):
            r = self.toggle_lifx(light, "on", color=color, brightness=brightness)
        else:
            r = self.toggle_webhook(light, "on", color=color, brightness=brightness)
        light.set_power(True)
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
        r = None
        if light.match_tags("wyze"):
            r = self.toggle_wyze(light, "off")
        elif light.match_tags("lifx"):
            r = self.toggle_lifx(light, "off")
        else:
            r = self.toggle_webhook(light, "off")
        light.unlock() # release the light's lock
        light.set_power(False)
        return r

    # Adds a power_off action to the thread queue for asynchronous processing.
    def queue_power_off(self, lid):
        a = LumenThreadQueueAction("off", lid)
        self.log.write("Queueing OFF action for %s." % lid)
        self.queue.push(a)

    # ------------------------------- Helpers -------------------------------- #
    # Uses IFTTT webhooks to toggle a light.
    def toggle_webhook(self, light: Light, action: str, color=None, brightness=None):
        action = action.strip().lower()
        assert action in ["on", "off"]

        # build a payload to send to IFTTT
        jdata = {"id": light.lid, "action": action}
        if color is not None:
            jdata["color"] = "%s,%s,%s" % (color[0], color[1], color[2])
        if brightness is not None:
            jdata["brightness"] = brightness

        # build a payload, update the light's current state, and send the
        # request to IFTTT
        light.set_power(True if action == "on" else False)
        return self.webhooker.send(self.config.webhook_event, jdata)

    # Uses the Wyze API to toggle a light.
    def toggle_wyze(self, light: Light, action: str, color=None, brightness=None):
        action = action.strip().lower()
        assert action in ["on", "off"]

        device = self.search_wyze(light.lid)
        if device is None:
            self.log.write("Could not find Wyze device with name \"%s\"." % light.lid)
            return

        # currently, only wyze plugs are supported
        if not light.match_tags("wyze-plug"):
            self.log.write("Wyze device \"%s\" is not a Wyze plug (not supported)." % light.lid)
            return

        power_on = True if action == "on" else False
        self.log.write("Toggling Wyze device \"%s\" to \"%s\"." % (light.lid, action))
        return self.wyze.toggle_plug(device.mac, power_on)

    # Uses the LIFX LAN SDK to toggle LIFX devices.
    def toggle_lifx(self, light: Light, action: str, color=None, brightness=None):
        action = action.strip().lower()
        assert action in ["on", "off"]

        # retrieve the device from the LIFX object
        l = self.lifx.get_light_by_name(light.lid)
        if l is None:
            self.log.write("LIFX device \"%s\" not found." % light.lid)
            return

        # toggle the light
        self.log.write("Toggling LIFX device \"%s\" to \"%s\"." % (light.lid, action))
        self.lifx.set_light_power(l, action)

        # if color and brightness was specified, apply it
        if color is not None:
            self.lifx.set_light_color(l, color)

        # if brightness was specified, apply it
        if brightness is not None:
            self.lifx.set_light_brightness(l, brightness)

    # Searches for a Wyze device with the given ID string and returns it (or
    # None).
    def search_wyze(self, lid: str):
        self.wyze.refresh()
        devices = self.wyze.get_devices()
        for dev in devices:
            if dev.nickname == lid:
                return dev
        return None

    # Searches lumen's light array and returns a Light object if one with a
    # matching light ID is found. Otherwise, None is returned.
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

    def init_nla(self):
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                    "name": "get_devices",
                    "description": "Retrieve information about the devices that Lumen can control."
                }).set_handler(nla_get),
            NLAEndpoint.from_json({
                    "name": "toggle_device",
                    "description": "Toggle a device on or off, set a device's color, set a device's brightness.\n" \
                                   "Phrases like \"turn ___ on\", \"set ___ to\", \"make ___ X%%\", etc., are examples of what to look for."
                }).set_handler(nla_toggle),
        ]


# =============================== NLA Handlers =============================== #
def nla_get(oracle: LumenOracle, jdata: dict):
    params = NLAEndpointInvokeParameters.from_json(jdata)

    # iterate through all the lights in the service and build a response
    # message containing all lights
    msg = ""
    device_strs = []
    for device in oracle.service.lights:
        device_strs.append("• %s - %s" % (device.lid, device.description))
    msg = "List of controllable devices:\n" + "\n".join(device_strs)

    return NLAResult.from_json({
        "success": True,
        "message": msg
    })

def nla_toggle(oracle: LumenOracle, jdata: dict):
    params = NLAEndpointInvokeParameters.from_json(jdata)

    # build an array of strings to represent all supported devices
    device_str = ""
    for device in oracle.service.lights:
        device_str += "• %s - %s\n" % (device.lid, device.description)
        device_str += "    • Tags: %s\n" % str(device.tags)

    # set up an intro prompt for the LLM
    prompt_intro = "You are a home assistant specializing in toggling smart home devices.\n " \
                   "You have access to the following devices:\n\n%s\n" \
                   "You will receive a command from a user to toggle one or more of these devices.\n" \
                   "Your task is to:\n\n" \
                   "1. Identify the ID strings of the device(s) to be toggled, using the tags and device descriptions.\n" \
                   "    * The tags are keywords that represent the categories and groups these lights fall into.\n" \
                   "    * If you see a device's tag mentioned in the message, there is a good chance the user is referring to that device.\n" \
                   "2. Determine if the user wants the device(s) turned on or off.\n" \
                   "3. If the user wants a device turned on, determine if a specific color or brightness is requested.\n" \
                   "    * The color should be a list of three RGB integers between 0 and 255 (inclusive).\n" \
                   "    * The brightness should be a float between 0.0 and 1.0 (inclusive).\n" \
                   "    * If no color is specified, omit the field from the JSON object.\n" \
                   "    * If no brightness is specified, omit the field from the JSON object.\n" \
                   "\n" \
                   "You must respond in the following JSON format:\n\n" \
                   "[\n" \
                   "    {\n" \
                   "        \"id\": \"ID_STRING_OF_DEVICE\",\n" \
                   "        \"action\": \"'ON' or 'OFF'\",\n" \
                   "        \"color\": [255, 255, 255],\n" \
                   "        \"brightness\": 0.5\n" \
                   "    },\n" \
                   "    {\n" \
                   "        \"id\": \"ID_STRING_OF_DEVICE\",\n" \
                   "        \"action\": \"'ON' or 'OFF'\",\n" \
                   "        \"color\": \"\"255,255,255\",\n" \
                   "        \"brightness\": 0.5\n" \
                   "    }\n" \
                   "]\n\n" \
                   "If you cannot identify any devices to toggle, respond with an empty JSON list: []\n\n" \
                   "Please only respond with the JSON list, and nothing else." \
                   % device_str

    # build the user-message prompt
    prompt_content = params.message
    if hasattr(params, "substring"):
        prompt_content = params.substring

    # set up a dialogue interface
    dialogue = DialogueInterface(oracle.service.config.dialogue)

    # repeatedly prompt the LLM until we get a valid response (or run out of
    # tries)
    action_list = []
    fail_count = 0
    for attempt in range(oracle.service.config.nla_toggle_dialogue_retries):
        try:
            r = dialogue.oneshot(prompt_intro, prompt_content)
            action_list = json.loads(r)
            if type(action_list) != list:
                raise Exception("LLM response is not a JSON list.")

            # sanitize for bad formatting
            for entry in action_list:
                if "id" not in entry or \
                   "action" not in entry:
                    raise Exception("LLM response is missing required fields.")

                # if color was provided, make sure it's a list of 3 integers
                if "color" in entry and not \
                   (type(entry["color"]) == list and
                    len(entry["color"]) == 3):
                    raise Exception("LLM response has invalid color field.")

                # if brightness was provided, make sure it's a float between
                # 0.0 and 1.0
                if "brightness" in entry and not \
                   (type(entry["brightness"]) in [float, int] and
                    entry["brightness"] >= 0.0 and
                    entry["brightness"] <= 1.0):
                    raise Exception("LLM response has invalid brightness field.")

            # break on the first success
            break
        except Exception as e:
            oracle.service.log.write("Failed to parse LLM response: %s" % e)
            fail_count += 1
            continue

    # if we couldn't parse the response, return an error message
    if fail_count == oracle.service.config.nla_toggle_dialogue_retries:
        return NLAResult.from_json({
            "success": False,
            "message": "Something went wrong while trying to find devices to toggle."
        })

    # otherwise, if there were *no* devices to toggle, return a message
    action_list_len = len(action_list)
    if action_list_len == 0:
        return NLAResult.from_json({
            "success": True,
            "message": "I couldn't find any devices to toggle based on your request."
        })

    # finally, iterate through the action list and submit toggles to the
    # service. At the same time, build a response message
    device_msgs = []
    for action_info in action_list:
        device_id = action_info["id"]

        # grab the action and invoke the appropriate service function
        action = action_info["action"].strip().lower()
        if action == "on":
            # get the color string, if one was provided
            color = None
            if "color" in action_info:
                color = action_info["color"]

            # get the brightness, if one was provided
            brightness = None
            if "brightness" in action_info:
                brightness = action_info["brightness"]
                # if brightness isn't a float, ignore it
                if type(brightness) != float:
                    brightness = None

            # queue the power-on action
            oracle.service.queue_power_on(device_id,
                                          color=color,
                                          brightness=brightness)

            # compose a message for this device
            device_msg = "• I turned \"%s\" on" % device_id
            if color is not None:
                device_msg += " with color RGB(%d, %d, %d)" % (color[0], color[1], color[2])
            if brightness is not None:
                device_msg += " at brightness %d%%" % (int(brightness * 100.0))
            device_msg += "."
            device_msgs.append(device_msg)
        elif action == "off":
            oracle.service.queue_power_off(device_id)

            # compose a message for this device
            device_msg = "• I turned \"%s\" off." % device_id
            device_msgs.append(device_msg)
        else:
            oracle.service.log.write("Unknown action \"%s\" for device \"%s\"." % (action, device_id))


    # compose a final response message
    msg = "I did the following:\n\n%s" % "\n".join(device_msgs)

    # give some additional context about interpreting/rewording the message
    msg_ctx = "When rewording this, please turn the device ID strings into human-friendly names.\n" \
              "Additionally, if you see any colors, please interpret the RGB values and replace them with a fitting common color name.\n"

    return NLAResult.from_json({
        "success": True,
        "message": msg,
        "message_context": msg_ctx,
    })


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=LumenConfig, service=LumenService, oracle=LumenOracle)
    cli.run()

