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

# Main function.
def command_lights(service, message, args: list):
    # create a HTTP session with lumen
    session = OracleSession(service.config.lumen_address,
                            service.config.lumen_port)
    try:
        r = session.login(service.config.lumen_auth_username,
                            service.config.lumen_auth_password)
    except Exception as e:
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't reach Lumen. "
                                "It might be offline.")
        return False
    
    # check the login response
    if r.status_code != 200:
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't authenticate with Lumen.")
        return False
    if not session.get_response_success(r):
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't authenticate with Lumen. "
                                "(%s)" % session.get_response_message(r))
        return False
    
    # if no other arguments were specified, we'll generate a list of names
    # for the lights around the house
    if len(args) == 1:
        r = session.get("/lights")
        try:
            lights = session.get_response_json(r)
            msg = "<b>All connected lights</b>\n\n"
            for light in lights:
                msg += "• <code>%s</code> - %s\n" % \
                            (light["id"], light["description"])
            service.bot.send_message(message.chat.id, msg, parse_mode="HTML")
            return True
        except Exception as e:
            service.bot.send_message(message.chat.id,
                                     "Sorry, I couldn't retrieve light data. "
                                     "(%s)" % e)
            return False

        msg = "I'm not sure what you meant."
        service.bot.send_message(message.chat.id, msg)

