# This module defines classes/functions used to represent a single event. Events
# have a specific JSON format and are received via an endpoint in the Herald
# oracle. Each event can have a number of configurable "subscriber" scripts that
# are executed whenever a specified event arrives.

# Imports
import os
import sys
import json

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# =============================== Event Config =============================== #
class HeraldEventConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("subscribers",  [list],     required=True)
        ]


# =============================== Event Class ================================ #
class HeraldEvent:
    # Constructor. Takes in JSON data and parses it as a HeraldEventConfig.
    def __init__(self, jdata: dict):
        self.config = HeraldEventConfig()
        self.config.parse_json(jdata)

        # iterate through each subscriber and make sure the file exists. Also,
        # import each subscriber's code
        for sub in self.config.subscribers:
            assert os.path.isfile(sub), \
                   "the subscriber script \"%s\" does not exist!" % sub

            # TODO - develop event subscriber class and use importlib here to
            # dynamically import the class

    # String representation.
    def __str__(self):
        return "%s: %d subscribers" % (self.config.name, len(self.config.subscribers))
    
    # Fires the event, which executes all subscribers.
    def fire(self):
        # TODO
        pass

