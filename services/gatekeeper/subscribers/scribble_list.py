#!/usr/bin/env python3
# This routine is invoked when I ping the gatekeeper to perform some operation
# on a list with Scribble.

# Imports
import os
import sys
import json
import requests
import time
from datetime import datetime

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.mail import MessengerConfig, Messenger
from lib.oracle import OracleSession

# Globals
scribble_config_path = "/home/provolone/chs/services/scribble/cwshugg_scribble.json"
scribble_config_data = None
scribble_url_base = None
scribble_session = None
mail_config_name = "cwshugg_mail_config.json"
mail_config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), mail_config_name)


# Initializes the HTTP session with scribble.
def scribble_init():
    # open and read the config file, if necessary
    global scribble_config_data
    if scribble_config_data is None:
        # parse the scribble config file
        scribble_config_data = None
        with open(scribble_config_path, "r") as fp:
            scribble_config_data = json.load(fp)

    # build a URL base for requests
    global scribble_url_base
    scribble_url_base = "http://%s:%d" % (scribble_config_data["oracle_addr"],
                                          scribble_config_data["oracle_port"])
    
    # set up the session, only the first time
    global scribble_session
    if scribble_session is None:
        scribble_session = OracleSession(scribble_config_data["oracle_addr"],
                                         scribble_config_data["oracle_port"])
        # authenticate with the service
        users = scribble_config_data["oracle_auth_users"]
        user = users[0]
        scribble_session.login(user["username"], user["password"])

# Helper function for talking with Scribble.
def scribble_send(data: dict):
    # extract the action string from the JSON payload
    assert "action" in data, "no \"action\" given in data"
    action = data["action"].strip().lower()

    # build a URL and any other parameters
    endpoint = "/"
    method = "POST"
    payload = {}
    if action == "get_all":
        endpoint = "/list/get/all"
        method = "GET"
    elif action == "get":
        endpoint = "/list/get"
        assert "name" in data
        payload["name"] = data["name"]
    elif action == "create":
        endpoint = "/list/create"
        assert "name" in data
        payload["name"] = data["name"]
    elif action == "delete":
        endpoint = "/list/delete"
        assert "name" in data
        payload["name"] = data["name"]
    elif action == "append":
        endpoint =  "/list/append"
        assert "name" in data
        assert "item" in data
        payload["name"] = data["name"]
        payload["item"] = data["item"]
    elif action == "remove":
        endpoint = "/list/remove"
        assert "name" in data
        assert "item" in data
        payload["name"] = data["name"]
        payload["item"] = data["item"]
        # TODO - fix this one. It requires an item ID
    else:
        assert False, "unknown action: \"%s\"" % action


    # send the request
    print("Sending Scribble \"%s\" request: %s %s" % (action, endpoint, json.dumps(payload)))
    r = None
    if method == "GET":
        r = scribble_session.get(endpoint)
    else:
        r = scribble_session.post(endpoint, payload=payload)
    print("Scribble response: %d (%s)" % (r.status_code, json.dumps(r.json(), indent=4)))
    return r

# Sends an email message with the response data.
def email_response(data: dict, rdata: dict):
    # ------------------------------- Helpers -------------------------------- #
    # Builds HTML for one list.
    def summarize_list(ldata: dict):
        html = "<h3>%s</h3>" % ldata["name"]
        html += "<ul>"
        for item in ldata["items"]:
            html += "<li>%s</li>" % item["text"]
        html += "</ul>"
        return html

    # ----------------------------- Runner Code ------------------------------ #
    # build a subject line
    subject = "DImROD Scribble - "
    action = data["action"].strip().lower()
    is_get = action in ["get", "get_all"]
    if is_get:
        subject += "Retrieval %s" % ("Success" if rdata["success"] else "Failure")
    else:
        subject += "Operation %s" % ("Success" if rdata["success"] else "Failure")
    
    # build an email body
    header = "Scribble Response"
    if is_get:
        header = "Single List Retrieval" if action == "get" else "All Lists"
    msg = "<h1>%s</h1>" % header
    if "message" in rdata and len(rdata["message"]) > 0:
        msg += "<p>%s</p>" % rdata["message"]
    if is_get:
        payload = rdata["payload"]
        # handle cases where either ONE or MANY lists are returned
        if type(payload) == dict:
            msg += summarize_list(payload)
        elif type(payload) == list:
            for ldata in payload:
                msg += summarize_list(ldata)
    
    # build a messenger object to send emails
    mconf = MessengerConfig()
    mconf.parse_file(mail_config_path)
    m = Messenger(mconf)

    # for each email address, send the data
    assert "emails" in data
    for addr in data["emails"]:
        print("Sending summary of results to %s..." % addr)
        m.send(addr, subject, msg)

# Main function.
def main():
    # check command-line arguments and attempt to parse as JSON
    data = {}
    if len(sys.argv) > 1:
        data = json.loads(sys.argv[1])
    
    # set up an authenticated session with scribble, then send the request
    scribble_init()
    r = scribble_send(data)

    # take the response data and form an email message
    rdata = r.json()
    if "emails" in data:
        email_response(data, rdata)

# Runner code
if __name__ == "__main__":
    sys.exit(main())

