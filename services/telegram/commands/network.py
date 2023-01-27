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

# Main function
def command_network(service, message, args: list):
    # create a HTTP session with warden
    session = OracleSession(service.config.warden_address,
                            service.config.warden_port)
    try:
        r = session.login(service.config.warden_auth_username,
                            service.config.warden_auth_password)
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

    # if no arguments are specified, we'll list the connected devices
    if len(args) == 1:
        msg = "<b>All cached devices</b>\n\n"
        r = session.get("/clients")
        try:
            clients = session.get_response_json(r)
            clients = reversed(sorted(clients, key=lambda c: c["last_seen"]))
            for client in clients:
                last_seen = datetime.fromtimestamp(client["last_seen"])
                cname_str = ""
                if "name" in client:
                    cname_str = " (<i>%s</i>)" % client["name"]
                msg += "• <code>%s</code>%s\n" % (client["macaddr"], cname_str)
                msg += "    • <b>IP Address:</b> <code>%s</code>\n" % client["ipaddr"]
                msg += "    • <b>Last seen:</b> %s\n" % \
                        last_seen.strftime("%Y-%m-%d at %I:%M:%S %p")
            service.send_message(message.chat.id, msg, parse_mode="HTML")
            return True
        except Exception as e:
            service.send_message(message.chat.id,
                                 "Sorry, I couldn't retrieve network data. "
                                 "(%s)" % e)
            return False

    msg = "I'm not sure what you meant."
    service.send_message(message.chat.id, msg)
