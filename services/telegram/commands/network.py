# Implements the /network bot command.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession


# ================================= Helpers ================================== #
# Creates and sends a list of cached devices, sorted by last-seen time.
def network_list_times(service, message, args, clients):
    msg = "<b>All Cached Devices</b>\n\n"

    # sort the clients into buckets based on last-seen time
    buckets = [
        {"name": "Currently online",                        "time": 120,    "list": []},
        {"name": "Last seen 5 minutes ago",                 "time": 300,    "list": []},
        {"name": "Last seen 5-15 minutes ago",              "time": 900,    "list": []},
        {"name": "Last seen 15-30 minutes ago",             "time": 1800,   "list": []},
        {"name": "Last seen within the last hour",          "time": 3600,   "list": []},
        {"name": "Last seen within the last four hours",    "time": 14400,  "list": []},
        {"name": "Last seen within the last eight hours",   "time": 28800,  "list": []},
        {"name": "Last seen within the last day",           "time": 86400,  "list": []},
        {"name": "Last seen within the last two days",      "time": 172800,  "list": []},
        {"name": "Last seen within the last three days",    "time": 259200,  "list": []},
        {"name": "Last seen within the last four days",     "time": 345600,  "list": []},
        {"name": "Last seen within the last five days",     "time": 432000,  "list": []},
        {"name": "Last seen within the last six days",      "time": 518400,  "list": []},
        {"name": "Last seen within the last week",          "time": 604800, "list": []},
        {"name": "Last seen within the last two weeks",     "time": 1209600, "list": []},
        {"name": "Last seen within the last month",         "time": 2419200, "list": []},
        {"name": "Last seen within the last two months",    "time": 4838400, "list": []},
        {"name": "Last seen within the last four months",   "time": 9676800, "list": []},
        {"name": "Last seen within the last year",          "time": 29030400, "list": []}
    ]
    now = datetime.now()
    for client in clients:
        last_seen = datetime.fromtimestamp(client["last_seen"])
        diff = now.timestamp() - last_seen.timestamp()
        for b in buckets:
            # if the time since last seen fits in the bucket's time
            # window, add it to the bucket
            if int(diff) <= int(b["time"]):
                b["list"].append(client)
                break
    
    # now, prepare a message listing off any non-empty buckets
    for b in buckets:
        if len(b["list"]) == 0:
            continue
        msg += "<b>%s:</b>\n" % b["name"]
        for client in b["list"]:
            # add the client's name or MAC address to the message
            if "name" in client:
                msg += "• <i>%s</i>" % client["name"]
            else:
                msg += "• <code>%s</code>" % client["macaddr"]
            # add the last-seen time (if it's on the same day, don't
            # include the day in the date string)
            last_seen = datetime.fromtimestamp(client["last_seen"])
            dtstr = last_seen.strftime("%I:%M:%S %p")
            if now.year != last_seen.year or \
                now.month != last_seen.month or \
                now.day != last_seen.day:
                dtstr = "%s at %s" % (last_seen.strftime("%Y-%m-%d"), dtstr)
            msg += " - %s\n" % dtstr
        msg += "\n"
    
    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True

# Creates and sends a list of comprehensive information for all cached devices.
def network_list_info(service, message, args, clients):
    msg = "<b>All Cached Devices</b>\n\n"
            
    for client in clients:
        last_seen = datetime.fromtimestamp(client["last_seen"])
        cname_str = ""
        if "name" in client:
            cname_str = " (<i>%s</i>)" % client["name"]
            msg += "• <code>%s</code>%s\n" % (client["macaddr"], cname_str)
            msg += "    • <b>IP Address:</b> <code>%s</code>\n" % client["ipaddr"]
            msg += "    • <b>Last seen:</b> %s\n" % last_seen.strftime("%Y-%m-%d at %I:%M:%S %p")
    
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# =================================== Main =================================== #
def command_network(service, message, args: list):
    # create a HTTP session with warden
    session = OracleSession(service.config.warden)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Warden. "
                             "It might be offline.")
        return False

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Warden.")
        return False
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Warden. "
                             "(%s)" % session.get_response_message(r))
        return False

    # first, retrieve a list of clients from warden (sorted by last_seen)
    clients = []
    try:
        r = session.get("/clients")
        clients = session.get_response_json(r)
        clients = reversed(sorted(clients, key=lambda c: c["last_seen"]))
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve a list of clients from Warden. "
                             "(%s)" % e)
        return False

    # if no arguments are specified, we'll list the connected devices
    if len(args) == 1:
        try:
            network_list_times(service, message, args, clients)
            return True
        except Exception as e:
            service.send_message(message.chat.id,
                                 "Sorry, I couldn't retrieve network data. "
                                 "(%s)" % e)
            return False

    # otherwise, look for sub-commands
    subcmd = args[1].strip().lower()
    if subcmd in ["clients", "info"]:
        try:
            network_list_info(service, message, args, clients)
            return True
        except Exception as e:
            service.send_message(message.chat.id,
                                 "Sorry, I couldn't retrieve network data. "
                                 "(%s)" % e)
            return False

    msg = "I'm not sure what you meant."
    service.send_message(message.chat.id, msg)
