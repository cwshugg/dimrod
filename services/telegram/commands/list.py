# Implements the /list bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Main function.
def command_list(service, message, args: list):
    msg = "TODO - list"
    service.bot.send_message(message.chat.id, msg)

