#!/usr/bin/env python3
# This routine checks for when DImROD's moder changes modes.

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

# Globals
moder_config_path = os.path.join(pdir, "services/moder/cwshugg_moder.json")
last_mode_fpath = os.path.join(os.path.dirname(__file__), ".mode_switch.last_mode.pkl")

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
    # load configs and start sessions
    moder_config = get_config(moder_config_path)
    ms = get_session(moder_config)

    # retrieve the mode from moder
    r = ms.get("/mode/get")
    jdata = ms.get_response_json(r)
    mode = jdata["mode"]

    # read the last mode from the previous invocation, if possible
    if os.path.isfile(last_mode_fpath):
        fp = open(last_mode_fpath, "rb")
        lm = pickle.load(fp)
        fp.close()

        # if the current mode differs, there's been a switch
        print("CURRENT MODE: %s" % mode)
        print("OLD MODE:     %s" % lm)
        if lm != mode:
            msgdata = {
                "message": "Switched modes to %s." % mode,
                "title": "DImROD Mode Switch",
                "tags": ["mode"],
            }
            r = ms.post("/msghub/post", payload=msgdata)
            print("Message send response: %s" % r)

    # save the current mode to the file
    fp = open(last_mode_fpath, "wb")
    pickle.dump(mode, fp)
    fp.close()

# Runner code
if __name__ == "__main__":
    sys.exit(main())

