# Implements the /_menu secret bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from menu import MenuConfig

# Main function.
def command_s_menu(service, message, args: list):
    mconf = MenuConfig()
    mconf.parse_json({
        "title": "This is a menu",
        "children": [
            {
                "title": "A",
                "children": []
            },
            {
                "title": "B",
                "children": [
                    {
                        "title": "This is B's inner menu",
                        "children": [
                            {
                                "title": "B1",
                                "children": []
                            },
                            {
                                "title": "B2",
                                "children": []
                            }
                        ]
                    }
                ]
            },
            {
                "title": "C",
                "children": []
            },
        ]
    })
    service.send_menu(message.chat.id, mconf)

