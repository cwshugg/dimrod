#!/usr/bin/env python3
# This subscriber script is invoked when motion is detected outside the front of
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
light_colors = [
    # MORNING - red to white
    [255,   0,      0],     # 12:00 am
    [255,   65,     36],    # 1:00 am
    [255,   96,     63],    # 2:00 am
    [255,   122,    89],    # 3:00 am
    [255,   146,    116],   # 4:00 am
    [255,   168,    142],   # 5:00 am
    [255,   190,    170],   # 6:00 am
    [255,   212,    197],   # 7:00 am
    [255,   234,    226],   # 8:00 am
    [255,   255,    255],   # 9:00 am
    # DAY - white to blue to white
    [215,   221,    255],   # 10:00 am
    [171,   187,    255],   # 11:00 am
    [118,   156,    255],   # 12:00 pm
    [0,     126,    255],   # 1:00 pm
    [118,   156,    255],   # 2:00 pm
    [171,   187,    255],   # 3:00 pm
    [215,   221,    255],   # 4:00 pm
    [255,   255,    255],   # 5:00 pm
    # EVENING
    [255,   225,    220],   # 6:00 pm
    [255,   225,    220],   # 7:00 pm
    [255,   220,    215],   # 8:00 pm
    [255,   220,    215],   # 9:00 pm
    [255,   215,    210],   # 10:00 pm
    [255,   215,    210],   # 11:00 pm
]

# Helper function for talking with Lumen.
def lumen_send(lid: str, action: str, color=None, brightness=None):
    # read the JSON config file to get Lumen credentials and create a session
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

# Takes in the current datetime and returns a color to use for the front light.
def get_color(now: datetime):
    # halloween colors
    if now.month == 10:
        # at night: spooky purple
        if now.hour in range(0, 5) or now.hour in range(21, 24):
            return [92, 88, 164]
        # morning/evening: orange
        elif now.hour in range(5, 10) or now.hour in range(19, 21):
            return [248, 115, 30]
        # early/late afternoon: yellow
        elif now.hour in range(10, 1) or now.hour in range(16, 19):
            return [255, 212, 1]
        # mid afternoon: green
        else:
            return [181, 200, 2]
    # christmas colors
    elif now.month == 12:
        # at night: candle-ish
        if now.hour in range(0, 5) or now.hour in range(21, 24):
            return [205, 147, 109]
        # morning/evening: red
        elif now.hour in range(5, 10) or now.hour in range(19, 21):
            return [219, 112, 118]
        # early/late afternoon: green
        elif now.hour in range(10, 1) or now.hour in range(16, 19):
            return [122, 155, 100]
        # mid afternoon: snow
        else:
            return [216, 230, 243]

    # default: warm white
    return [255, 235, 225]

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])

    # ---------------------- Color/Brightness Decision ----------------------- #
    now = datetime.now()
    color = get_color(now)
    print("COLOR: %s" % color)
    brightness = 1.0

    # ------------------------------ Lights On ------------------------------- #
    lumen_send("bulb_front_porch", "on", color=color, brightness=brightness)

    # wait a specified amount of time, then turn the light back off
    time.sleep(light_cooldown)

    # ------------------------------ Lights Off ------------------------------ #
    lumen_send("bulb_front_porch", "off")

# Runner code
if __name__ == "__main__":
    sys.exit(main())

