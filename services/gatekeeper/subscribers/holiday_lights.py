#!/usr/bin/env python3
# This subscriber script is invoked when toggling the house's decorations for
# various holidays (Halloween, Christmas, etc.)

# Imports
import os
import sys
import json
import requests
import time
from datetime import datetime, timezone
from dateutil import parser

import geopy.geocoders
import timezonefinder
import pytz

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
sunrise_window = 1800
sunset_window = 1800

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
    if len(sys.argv) > 1 and len(sys.argv[1]) > 0:
        data = json.loads(sys.argv[1])
    
    # we'll use the datetime, as well as the sunset time based on the house's
    # longitude and latitude, to determine what time the sun sets, and when to
    # turn the lights on and off
    now = datetime.now()
    latitude = 35.786168069281715   # default: raleigh, nc
    longitude = -78.68165659384003  # default: raleigh, nc
    if "address" not in data:
        print("No address provided. Assuming default longitude and latitude.")
    else:
        addr = data["address"]
        loc = geopy.geocoders.Nominatim(user_agent="dimrod").geocode(addr)
        latitude = loc.latitude
        longitude = loc.longitude
    print("Longitude:   %s" % longitude)
    print("Latitude:    %s" % latitude)
    
    # make an API request to retrieve the sunrise/sunset times for today
    sunrise = now.replace(hour=6, minute=0, second=0)
    sunset = now.replace(hour=18, minute=0, second=0)
    try:
        r = requests.get("https://api.sunrise-sunset.org/json",
                         params={"lat": latitude, "lng": longitude})
        jdata = r.json()["results"]

        # use the latitude and longitude to determine the timezone to convert to
        tzname = timezonefinder.TimezoneFinder().timezone_at(lng=longitude, lat=latitude)
        tz = pytz.timezone(tzname)
        print("Timezone:    %s" % tz)

        # parse sunrise and sunset
        sunrise = parser.parse("%s %s" % (now.strftime("%Y-%m-%d"), jdata["sunrise"]))
        sunrise = sunrise.replace(tzinfo=timezone.utc).astimezone(tz)
        sunset = parser.parse("%s %s" % (now.strftime("%Y-%m-%d"), jdata["sunset"]))
        sunset = sunset.replace(tzinfo=timezone.utc).astimezone(tz)
    except Exception as e:
        print("Failed to retireve sunrise/sunset times: %s" % e)

    print("Sunrise:     %s" % sunrise.strftime("%H:%M:%S %p"))
    print("Sunset:      %s" % sunset.strftime("%H:%M:%S %p"))
    
    # ------------------------------- Holidays ------------------------------- #
    if now.month in [10, 12]:
        sunset_diff = abs(now.timestamp() - sunset.timestamp())
        sunrise_diff = abs(now.timestamp() - sunrise.timestamp())
        print("Seconds away from sunrise: %d (window is %d)" % (sunrise_diff, sunrise_window))
        print("Seconds away from sunset:  %d (window is %d)" % (sunset_diff, sunset_window))

        # if we're within the threshold, turn the lights on or off
        if sunset_diff < sunset_window:
            print("Turning the lights on.")
            lumen_send("plug_front_porch1", "on")
            lumen_send("plug_front_porch2", "on")
        elif sunrise_diff < sunrise_window:
            print("Turning the lights off.")
            lumen_send("plug_front_porch1", "off")
            lumen_send("plug_front_porch2", "off")
        return

# Runner code
if __name__ == "__main__":
    sys.exit(main())

