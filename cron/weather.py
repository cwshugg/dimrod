#!/usr/bin/env python3
# This routine checks the current weather forecast by contacting Nimbus, and
# sends a message via Telegram to warn of any weather conditions.

# Imports
import os
import sys
import json
from datetime import datetime

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from services.lib.oracle import OracleSession

# Globals
nimbus_config_path = os.path.join(pdir, "services/nimbus/cwshugg_nimbus.json")
telegram_config_path = os.path.join(pdir, "services/telegram/cwshugg_telegram.json")
telegram_chat_names = ["cwshugg"]

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
    nimbus_config = get_config(nimbus_config_path)
    telegram_config = get_config(telegram_config_path)
    ns = get_session(nimbus_config)
    ts = get_session(telegram_config)
    
    # -------------------------- Weather Retrieval --------------------------- #
    now = datetime.now()
    # get the current weather at home, at night
    night = datetime(now.year, now.month, now.day, hour=23, minute=30, second=0)
    night_data = {"name": "home", "when": night.timestamp()}
    r = ns.post("/weather/byname", payload=night_data)
    weather_night = ns.get_response_json(r)
    msg = None
    
    # CASE 1 - freezing overnight!
    if weather_night["temperature_value"] <= 32:
        msg = "It's going to freeze tonight at home!\n" \
              "Keep the house warm and protect the car windshields from ice!"

    # if we don't have a message, go no further
    if msg is None:
        return

    # --------------------------- Telegram Message --------------------------- #
    # get all telegram chats the bot has whitelisted
    r = ts.get("/bot/chats")
    chats = ts.get_response_json(r)

    # find my private chat and send a message
    for chat in chats:
        for cname in telegram_chat_names:
            if cname.lower() in chat["name"].lower():
                print("Sending message to Telegram chat %s (%s)." %
                      (chat["id"], chat["name"]))
                mdata = {
                    "chat": chat,
                    "message": msg
                }
                ts.post("/bot/send", payload=mdata)

# Runner code
if __name__ == "__main__":
    sys.exit(main())

