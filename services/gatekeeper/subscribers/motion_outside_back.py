#!/usr/bin/env python3
# This subscriber script is invoked when motion is detected outside the back of
# the house.

# Imports
import os
import sys
import json
import requests
import time
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
light_cooldown = 180

# Helper function for talking with Lumen.
def lumen_send(lid: str, action: str, color=None, brightness=None):
    # open and read the config file, if necessary
    global lumen_config_data
    if lumen_config_data is None:
        # parse the lumen config file
        lumen_config_data = None
        with open(lumen_config_path, "r") as fp:
            lumen_config_data = json.load(fp)
    
    # set up the session, only the first time
    global lumen_session
    if lumen_session is None:
        lumen_session = OracleSession(lumen_config_data["oracle"]["addr"],
                                      lumen_config_data["oracle"]["port"])
        # authenticate with the service
        users = lumen_config_data["oracle"]["auth_users"]
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

    # ---------------------- Color/Brightness Decision ----------------------- #
    now = datetime.now()
    brightness = 1.0

    # ------------------------------ Lights On ------------------------------- #
    lumen_send("bulb_back_deck", "on", color=None, brightness=brightness)

    # wait a specified amount of time, then turn the light back off
    time.sleep(light_cooldown)

    # ------------------------------ Lights Off ------------------------------ #
    lumen_send("bulb_back_deck", "off")

# Runner code
if __name__ == "__main__":
    sys.exit(main())

