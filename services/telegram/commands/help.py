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

def command_help(service, message, args: list):
    """Main function. Displays a clean overview of all available commands."""
    # build a table of possible commands in HTML
    # https://core.telegram.org/bots/api#markdownv2-style
    msg = "🤖 <b>DImROD Commands</b>\n\n"
    for command in service.commands:
        # skip secret commands
        if not command.secret:
            msg += "/%s — %s\n" % (command.keywords[0], command.description)
    msg += "\nType any command with no arguments for detailed usage."
    service.send_message(message.chat.id, msg, parse_mode="HTML")

