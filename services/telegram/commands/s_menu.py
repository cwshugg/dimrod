# Implements the /_menu secret bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from menu import Menu

# Main function.
def command_s_menu(service, message, args: list):
    m = Menu()
    m.parse_json({
        "title": "This is a menu",
        "options": [
            {
                "title": "A",
            },
            {
                "title": "B",
            },
            {
                "title": "C",
            },
        ]
    })
    service.send_menu(message.chat.id, m)

