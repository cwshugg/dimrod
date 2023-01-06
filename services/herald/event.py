# This module defines classes/functions used to represent a single event. Events
# have a specific JSON format and are received via an endpoint in the Herald
# oracle. Each event can have a number of configurable "subscriber" scripts that
# are executed whenever a specified event arrives.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Service imports
from subscriber import HeraldSubscriber


# =============================== Event Config =============================== #
# This config class represents the required data to be included in the Herald
# config file.
class HeraldEventConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("subscribers",  [list],     required=True)
        ]

# This config class represents the required data to be sent to Herald when an
# event is posted from across the internet (via the oracle).
class HeraldEventPostConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("data",         [dict],     required=False,     default=None)
        ]


# =============================== Event Class ================================ #
class HeraldEvent:
    # Constructor. Takes in JSON data and parses it as a HeraldEventConfig.
    def __init__(self, jdata: dict):
        self.config = HeraldEventConfig()
        self.config.parse_json(jdata)

        # iterate through each subscriber and make sure the file exists. Also,
        # import each subscriber's code
        self.subscribers = []
        for sdata in self.config.subscribers:
            # parse each entry as a subscriber object
            sub = HeraldSubscriber(sdata)
            self.subscribers.append(sub)

    # String representation.
    def __str__(self):
        return "%s: %d subscribers" % (self.config.name, len(self.config.subscribers))
    
    # Fires the event, which executes all subscribers. Takes in an optional data
    # parameter. Returns a dictionary of stdout/stderr results corresponding to
    # each executed subscriber.
    def fire(self, data=None):
        # spawn each of the subscribers' child processes
        for sub in self.subscribers:
            sub.spawn(data=data)

        # now iterate, through the subscribers and wait for them to finish
        result = {}
        for sub in self.subscribers:
            (stdout, stderr) = sub.reap()
            result[sub.config.name] = {"stdout": stdout, "stderr": stderr}
        return result

