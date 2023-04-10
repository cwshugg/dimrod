#!/usr/bin/env python3
# This script is invoked to randomly turn lights on and off at home to fool
# onlookers into thinking we're home.

# Imports
import os
import sys
import json
import requests
import time
import random
from datetime import datetime

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession

# Globals
lumen_config_path = "/home/provolone/chs/services/lumen/cwshugg_lumen.json"
lumen_config_data = None
lumen_session = None
tick_rate = 300
light_ids = [
    "bulb_livingroom_1",
    "bulb_livingroom_2",
    "bulb_livingroom_3",
    "bulb_livingroom_4"
]

# Helper function for talking with Lumen.
def lumen_send(lid: str, action: str, color=None, brightness=None):
    # open and read the config file, if necessary
    global lumen_config_data
    if lumen_config_data is None:
        # parse the lumen config file
        lumen_config_data = None
        with open(lumen_config_path, "r") as fp:
            lumen_config_data = json.load(fp)
    
    # build a URL base for requests
    url_base = "http://%s:%d" % (lumen_config_data["oracle_addr"],
                                 lumen_config_data["oracle_port"])

    # set up the session, only the first time
    global lumen_session
    if lumen_session is None:
        lumen_session = OracleSession(lumen_config_data["oracle_addr"],
                                      lumen_config_data["oracle_port"])
        # authenticate with the service
        users = lumen_config_data["oracle_auth_users"]
        user = users[0]
        lumen_session.login(user["username"], user["password"])
    
    # now, take the parameters and build a request payload
    toggle_data = {
        "id": lid,
        "action": action
    }
    if color is not None:
        toggle_data["color"] = "%d,%d,%d" % (color[0], color[1], color[2])
    if brightness is not None:
        toggle_data["brightness"] = brightness

    # build the URL and send the request
    print("Sending Lumen toggle request: %s" % json.dumps(toggle_data))
    r = lumen_session.post("/toggle", payload=toggle_data)
    print("Lumen response: %d (%s)" % (r.status_code, json.dumps(r.json(), indent=4)))
    return r

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])

    print("Received data:\n%s" % json.dumps(data, indent=4))

    # parse the amount of time the caller wishes to randomize the lights
    duration = data["duration"]
    assert type(duration) == int, "'duration' must be an integer"

    # convert the duration into a specific time at which to stop
    start = datetime.now()
    end = datetime.fromtimestamp(start.timestamp() + duration)

    # seed the random generator
    random.seed()

    # ---------------------------- Light Foolery ----------------------------- #
    # loop until the ending timestamp has been reached
    now = start
    while now.timestamp() < end.timestamp():
        # select a random number of lights to toggle and iterate through them
        light_count = random.randrange(0, len(light_ids))
        light_ids_tmp = light_ids.copy()
        for i in range(light_count):
            # choose a random light ID from the tmp list
            light_id = light_ids_tmp.pop(random.randrange(0, len(light_ids_tmp)))

            # select a random action to perform (on vs. off)
            action = random.choice(["on", "off"])
            
            # send the request to lumen
            color = [255, 240, 240]
            brightness = 1.0
            lumen_send(light_id, action, color=color, brightness=brightness)

        # sleep until the next tick (or until the end)
        time_until_end = max(end.timestamp() - now.timestamp(), 0)
        time.sleep(min(tick_rate, time_until_end))
        now = datetime.now()

    # turn all the lights off
    print("Duration finished. Turning off all lights.")
    for lid in light_ids:
        lumen_send(lid, "off")

# Runner code
if __name__ == "__main__":
    sys.exit(main())

