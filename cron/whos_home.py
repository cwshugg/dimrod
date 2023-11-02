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

# Globals
warden_config_path = os.path.join(pdir, "services/warden/cwshugg_warden.json")
last_client_fpath = os.path.join(os.path.dirname(__file__), ".whos_home.last_client.pkl")

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
    warden_config = get_config(warden_config_path)
    ms = get_session(warden_config)

    # get list of clients from warden
    r = ms.get("/clients")
    new = ms.get_response_json(r)
    print(json.dumps(new, indent=4))

    # read the previous client list, if possible
    if os.path.isfile(last_client_fpath):
        fp = open(last_client_fpath, "rb")
        old = pickle.load(fp)
        fp.close()
        
        len_new = len(new)
        len_old = len(old)
        print("CURRENT CLIENTS: %d clients" % len(new))
        print("OLD CLIENTS:     %d clients" % len(old))

        # if the lengths differ, search for the new ones
        if len_new != len_old:
            # TODO
            pass
            # msgdata = {
            #     "message": "Switched modes to %s." % mode,
            #     "title": "DImROD Mode Switch",
            #     "tags": ["mode"],
            # }
            # r = ms.post("/msghub/post", payload=msgdata)
            # print("Message send response: %s" % r)

    # save the current client list to the file
    fp = open(last_client_fpath, "wb")
    pickle.dump(new, fp)
    fp.close()

# Runner code
if __name__ == "__main__":
    sys.exit(main())

