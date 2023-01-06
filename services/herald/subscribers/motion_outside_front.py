#!/usr/bin/env python3
# This subscriber script is invoked when motion is detected outside the front of
# the house.

# Imports
import sys
import json
import requests
from datetime import datetime

# Globals
lumen_config = "/home/provolone/chs/services/lumen/cwshugg_lumen.json"

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])
    
    # parse the lumen config file
    lumen_data = None
    with open(lumen_config, "r") as fp:
        lumen_data = json.load(fp)

    # retrieve a user to log in with
    users = lumen_data["oracle_auth_users"]
    user = users[0]

    # create a session and send a login request
    s = requests.Session()
    url_base = "http://%s:%d" % (lumen_data["oracle_addr"],
                                 lumen_data["oracle_port"])
    login_data = {"username": user["username"], "password": user["password"]}
    print("Logging into lumen... %s" % json.dumps(login_data))
    r = s.post(url_base + "/auth/login", json=login_data)
    print("Lumen response: %d (%s)" % (r.status_code, json.dumps(r.json(), indent=4)))

    # decide what color to set the front lights
    color = [255, 255, 255]
    now = datetime.now()
    if now.hour <= 5 or now.hour >= 22:     # during nighttime
        color = [255, 0, 0] # red
    color_str = "%d,%d,%d" % (color[0], color[1], color[2])

    # with the auth cookie stored, make a request to set the light color
    toggle_data = {
        "id": "bulb_front_porch",
        "action": "on",
        "color": color_str
    }
    url = url_base + "/toggle"
    print(url)
    r = s.post(url_base + "/toggle", json=toggle_data)
    print("Lumen response: %d (%s)" % (r.status_code, json.dumps(r.json(), indent=4)))

# Runner code
if __name__ == "__main__":
    sys.exit(main())

