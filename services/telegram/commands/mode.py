# Implements the /lights bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from lumen.light import LightConfig, Light


# =================================== Main =================================== #
# Main function.
def command_mode(service, message, args: list):
    # create a HTTP session with moder
    session = OracleSession(service.config.moder)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Moder. "
                             "It might be offline.")
        return False
    
    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Moder.")
        return False
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Moder. "
                             "(%s)" % session.get_response_message(r))
        return False

    # retrieve the list of available modes
    r = session.get("/mode/get_all")
    modes = []
    try:
        modes = session.get_response_json(r)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve available modes. (%s)" % e)
        return False
    
    # retrieve the current mode
    r = session.get("/mode/get")
    mode = None
    try:
        jdata = session.get_response_json(r)
        mode = jdata["mode"]
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve the current mode. (%s)" % e)
        return False
    
    # if no other arguments were specified, we'll respond with the current mode
    # and all available modes
    if len(args) <= 1:
        msg = "<b>All available modes</b>\n\n"
        for name in modes:
            msg += "• <code>%s</code>" % name
            # highlight the current mode
            if name == mode:
                msg += " - currently active"
            msg += "\n"
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return True
    
    # otherwise, interpret the next argument as the mode to queue, and the
    # following argument (if it exists) as the mode priority
    new_mode = args[1].strip().lower()
    new_priority = None
    if len(args) > 2:
        try:
            new_priority = int(args[2])
        except Exception as e:
            service.send_message(message.chat.id,
                                 "Your second argument must be a priority number.")
            return False
    
    # ping the mode-set endpoint
    pyld = {"mode": new_mode}
    if new_priority is not None:
        pyld["priority"] = new_priority
    r = session.post("/mode/queue", payload=pyld)

    # depending on the response, send a message back
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Failed to queue the new mode. (%s)" %
                             session.get_response_message(r))
        return False
    else:
        service.send_message(message.chat.id,
                             "Mode queued successfully.")
        return True

    # if all else fails, respond with an error message
    msg = "I'm not sure what you meant."
    service.send_message(message.chat.id, msg)

