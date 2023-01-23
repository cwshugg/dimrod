# Implements the /status bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Main function.
def command_status(service, message, args: list):
    msg = "DImROD is up and running."
    service.bot.send_message(message.chat.id, msg)

