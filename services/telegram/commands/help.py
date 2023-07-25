# Implements the /help bot command.

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
def command_help(service, message, args: list):
    # build a table of possible commands in HTML
    # https://core.telegram.org/bots/api#markdownv2-style
    msg = "<b>All possible commands</b>\n\n"
    for command in service.commands:
        # skip secret commands
        if not command.secret:
            msg += "/%s - %s\n" % (command.keywords[0], command.description)
    service.send_message(message.chat.id, msg, parse_mode="HTML")

