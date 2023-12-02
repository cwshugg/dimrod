#!/usr/bin/env python3
# This routine checks for when people home or leave home.

# Imports
import os
import sys
import json
from datetime import datetime
import pickle

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from services.lib.oracle import OracleSession
from services.lib.config import Config, ConfigField

# Globals
warden_config_path = os.path.join(pdir, "services/warden/cwshugg_warden.json")
last_client_fpath = os.path.realpath(os.path.join(os.path.dirname(__file__), ".whos_online.last_client.pkl"))
config_fpath = os.path.realpath(os.path.join(os.path.dirname(__file__), ".whos_online.json"))
threshold_online = 1200
threshold_offline = 1200


# ============================== Config Classes ============================== #
class WhosOnlineDeviceConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("macaddr",  [str],      required=True),
            ConfigField("owner",    [str],      required=True)
        ]

class WhosOnlineConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("devices", [WhosOnlineDeviceConfig], required=True)
        ]


# =============================== Runner Code ================================ #
# Parses a config file and returns the JSON data.
def get_config(path: str):
    fp = open(path, "r")
    jdata = json.load(fp)
    fp.close()
    return jdata

# Returns an OracleSession object that's been authenticated.
def get_session(conf: dict):
    s = OracleSession(conf["oracle_addr"], conf["oracle_port"])
    user = conf["oracle_auth_users"][0]
    s.login(user["username"], user["password"])
    return s

# Main function.
def main():
    # load script config
    if not os.path.isfile(config_fpath):
        sys.stderr.write("Error: no config file found at: %s\n" % config_fpath)
        sys.exit(1)
    conf_data = get_config(config_fpath)
    conf = WhosOnlineConfig()
    conf.parse_json(conf_data)
    print("CONFIG:\n%s" % conf)

    # load configs and start sessions
    warden_config = get_config(warden_config_path)
    ws = get_session(warden_config)

    # get list of clients from warden
    r = ws.get("/clients")
    new = ws.get_response_json(r)
    print("Received %d clients from warden." % len(new))

    # read the previous client list, if possible
    if os.path.isfile(last_client_fpath):
        fp = open(last_client_fpath, "rb")
        old = pickle.load(fp)
        fp.close()
        
        len_new = len(new)
        len_old = len(old)
        #print("CURRENT CLIENTS: %d clients" % len(new))
        #print("OLD CLIENTS:     %d clients" % len(old))

        # for each entry in the config, search for the device
        for device in conf.devices:
            d_old = None
            d_new = None
            d_status = None
            for d in old:
                if d["macaddr"] == device.macaddr:
                    d_old = d
                    if "status" in d_old:
                        d_status = d_old["status"]
                    break
            for d in new:
                if d["macaddr"] == device.macaddr:
                    d_new = d
                    d_new["status"] = None if d_status is None else d_status
                    break

            # if both are found, process
            if d_old is None or d_new is None:
                continue
            ls_old = int(d_old["last_seen"])
            ls_new = int(d_new["last_seen"])
            diff = ls_new - ls_old
            diff_now = ls_new - int(datetime.now().timestamp())
            print("Device \"%s\" was last seen %d seconds ago." % (d_new["name"], abs(diff_now)))
            #print("DIFF:     %d" % diff)
            #print("DIFF_NOW: %d" % diff_now)
            #print("DEVICE\n - %s\n - %s" % (d_old, d_new))

            # if the new timestamp is more recent, examine the old timestamp to
            # determine how long ago the device was last seen before
            # reconnecting. If it exceeds the threshold, we'll notify
            if diff > 0 and abs(diff) > threshold_online:
                # update the device's status and post a message to warden's msghub
                d_new["status"] = True
                msgdata = {
                    "message": "%s has come online." % d_new["name"],
                    "title": "DImROD Network",
                    "tags": ["network", "device"],
                }
                r = ws.post("/msghub/post", payload=msgdata)
                print("Device \"%s\" is online. Message send response: %s" % (d_new["name"], r))
                continue

            # otherwise, if the times are the sme, and they're past the offline
            # threshold, we'll notify (as long as we haven't notified recently
            # already)
            if diff == 0 and abs(diff_now) > threshold_offline and \
               d_status is not False:
                # update the device's status and post a message to warden's msghub
                d_new["status"] = False
                msgdata = {
                    "message": "%s has gone offline." % d_new["name"],
                    "title": "DImROD Network",
                    "tags": ["network", "device"],
                }
                r = ws.post("/msghub/post", payload=msgdata)
                print("Device \"%s\" is offline. Message send response: %s" % (d_new["name"], r))
                continue

    # save the current client list to the file
    fp = open(last_client_fpath, "wb")
    pickle.dump(new, fp)
    fp.close()

# Runner code
if __name__ == "__main__":
    sys.exit(main())

