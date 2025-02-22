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
cdir = os.path.realpath(os.path.dirname(__file__))
pdir = os.path.dirname(cdir)
maindir = os.path.dirname(pdir)
if maindir not in sys.path:
    sys.path.append(maindir)

# Local imports
from lib.oracle import OracleSession, OracleSessionConfig

# Globals
lumen_config_path = os.path.join(cdir, "cwshugg_lumen_config.json")
lumen_config = None
lumen_session = None
light_cooldown = 180

# Helper function for talking with Lumen.
def lumen_send(lid: str, action: str, color=None, brightness=None):
    # open and read the config file, if necessary
    global lumen_config
    if lumen_config is None:
        lumen_config = OracleSessionConfig()
        lumen_config.parse_file(lumen_config_path)
    
    # set up the session, only the first time
    global lumen_session
    if lumen_session is None:
        lumen_session = OracleSession(lumen_config)
        lumen_session.login()
    
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

