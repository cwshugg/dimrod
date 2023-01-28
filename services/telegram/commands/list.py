# Implements the /list bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

from lib.oracle import OracleSession

# Sends a summary of all lists.
def summarize(service, message, args, session):
    # TODO
    pass

# Main function.
def command_list(service, message, args: list):
    # create a session with scribble
    session = OracleSession(service.config.scribble_address,
                            service.config.scribble_port)
    try:
        r = session.login(service.config.scribble_auth_username,
                          service.config.scribble_auth_password)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Scribble. "
                             "It might be offline.")
        return False

    # retrieve all lists from scribble
    lists = []
    try:
        r = session.get("/lists/get/all")
        ldata = session.get_response_json(r)
        for l in ldata:
            print(l)

    # if no extra arguments were given, list all the available lists
    if len(args) == 1:
        summarize(service, message, args, session)


