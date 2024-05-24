#!/usr/bin/env python3
# This subscriber script is invoked when toggling the house's decorations for
# various holidays (Halloween, Christmas, etc.)

# Imports
import os
import sys
import json
import requests
import time
from datetime import datetime
import pickle

# Enable import from the main directory
fdir = os.path.dirname(__file__)
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
import lib.lu as lu

# Globals
lumen_config_path = "/home/provolone/chs/services/lumen/cwshugg_lumen.json"
lumen_config_data = None
lumen_session = None
sunrise_window = 1800
sunset_window = 1800
sunrise_msghub_file = os.path.realpath(os.path.join(fdir, ".holiday_lights_sunrise_last_msg_get.pkl"))
sunset_msghub_file = os.path.realpath(os.path.join(fdir, ".holiday_lights_sunset_last_msg_get.pkl"))

def sunrise_last_msg_get():
    if not os.path.isfile(sunrise_msghub_file):
        return None
    with open(sunrise_msghub_file, "rb") as fp:
        return pickle.load(fp)

def sunset_last_msg_get():
    if not os.path.isfile(sunset_msghub_file):
        return None
    with open(sunset_msghub_file, "rb") as fp:
        return pickle.load(fp)

def sunrise_last_msg_set(dt: datetime):
    with open(sunrise_msghub_file, "wb") as fp:
        pickle.dump(dt, fp)

def sunset_last_msg_set(dt: datetime):
    with open(sunset_msghub_file, "wb") as fp:
        pickle.dump(dt, fp)

def lumen_init():
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
        lumen_session = OracleSession(lumen_config_data["oracle_addr"],
                                      lumen_config_data["oracle_port"])
        # authenticate with the service
        users = lumen_config_data["oracle_auth_users"]
        user = users[0]
        lumen_session.login(user["username"], user["password"])
    

# Helper function for talking with Lumen.
def lumen_send(lid: str, action: str, color=None, brightness=None):
    lumen_init()

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
    if len(sys.argv) > 1 and len(sys.argv[1]) > 0:
        data = json.loads(sys.argv[1])
    
    # we'll use the datetime, as well as the sunset time based on the house's
    # longitude and latitude, to determine what time the sun sets, and when to
    # turn the lights on and off
    now = datetime.now()
    loc = None
    if "address" not in data:
        print("No address provided. Assuming default longitude and latitude.")
        loc = lu.Location(latitude=35.786168069281715, longitude=-78.68165659384003)
    else:
        loc = lu.Location(data["address"])
    
    # make an API request to retrieve the sunrise/sunset times for today
    sunrise = now.replace(hour=6, minute=0, second=0)
    sunset = now.replace(hour=18, minute=0, second=0)
    try:
        [sunrise, sunset] = lu.get_sunrise_sunset(loc=loc, dt=now)
    except Exception as e:
        raise e
        print("Failed to retireve sunrise/sunset times: %s" % e)

    print("Sunrise:     %s" % sunrise.strftime("%H:%M:%S %p"))
    print("Sunset:      %s" % sunset.strftime("%H:%M:%S %p"))
    
    # ------------------------------- Holidays ------------------------------- #
    if now.month in [10, 12]:
        sunset_diff = abs(now.timestamp() - sunset.timestamp())
        sunrise_diff = abs(now.timestamp() - sunrise.timestamp())
        print("Seconds away from sunrise: %d (window is %d)" % (sunrise_diff, sunrise_window))
        print("Seconds away from sunset:  %d (window is %d)" % (sunset_diff, sunset_window))

        # decide on the holiday name to use for any notifications sent
        holiday = "holiday"
        if now.month == 10:
            holiday = "Halloween"
        elif now.month == 12:
            holiday = "Christmas"

        # if we're within the threshold, turn the lights on or off
        if sunset_diff < sunset_window:
            print("Turning the lights on.")
            lumen_send("plug_front_porch1", "on")
            lumen_send("plug_front_porch2", "on")
            lumen_send("plug_back_deck1", "on")
            lumen_send("plug_back_deck2", "on")
            
            # send a message via lumen's message hub, as long as it wasn't sent
            # recently
            last_msg = sunset_last_msg_get()
            if last_msg is None or now.timestamp() - last_msg.timestamp() > sunset_window:
                lumen_init()
                msgdata = {
                    "message": "It's sunset. I'm turning on the %s lights." % holiday,
                    "title": "DImROD - Holiday Lights - ON",
                    "tags": ["holiday"],
                }
                r = lumen_session.post("/msghub/post", payload=msgdata)
                print("Message send response: %s" % r)
                sunset_last_msg_set(now)
            else:
                print("Message was last sent at %s. Skipping message send." %
                      last_msg.strftime("%Y-%m-%d %H:%M:%S %p"))
        elif sunrise_diff < sunrise_window:
            print("Turning the lights off.")
            lumen_send("plug_front_porch1", "off")
            lumen_send("plug_front_porch2", "off")
            lumen_send("plug_back_deck1", "off")
            lumen_send("plug_back_deck2", "off")
            
            # send a message via lumen's message hub, as long as it wasn't sent
            # recently
            last_msg = sunrise_last_msg_get()
            if last_msg is None or now.timestamp() - last_msg.timestamp() > sunrise_window:
                lumen_init()
                msgdata = {
                    "message": "It's sunrise. I'm turning off the %s lights." % holiday,
                    "title": "DImROD - Holiday Lights - OFF",
                    "tags": ["holiday"],
                }
                r = lumen_session.post("/msghub/post", payload=msgdata)
                print("Message send response: %s" % r)
                sunrise_last_msg_set(now)
            else:
                print("Message was last sent at %s. Skipping message send." %
                      last_msg.strftime("%Y-%m-%d %H:%M:%S %p"))
        return
    elif now.month in [1, 11] and now.day == 1:
        # on the first day of the months following holidays, make sure all
        # lights are turned off
        print("Holiday time is over. Turning off the lights.")
        lumen_send("plug_front_porch1", "off")
        lumen_send("plug_front_porch2", "off")
        lumen_send("plug_back_deck1", "off")
        lumen_send("plug_back_deck2", "off")
        
        # send a message to the lumen msghub
        msgdata = {
            "message": "It's sunset. I'm turning on the %s lights." % holiday,
            "title": "DImROD - Holiday Lights - ON",
            "tags": ["holiday"],
        }
        r = lumen_session.post("/msghub/post", payload=msgdata)
        print("Message send response: %s" % r)
        return
    
# Runner code
if __name__ == "__main__":
    sys.exit(main())

