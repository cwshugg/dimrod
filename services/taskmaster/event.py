# This module defines classes/functions used to represent a single event. Events
# have a specific JSON format and are received via an endpoint in the Taskmaster
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
from subscriber import TaskmasterSubscriber


# =============================== Event Config =============================== #
# This config class represents the required data to be included in the Taskmaster
# config file.
class TaskmasterEventConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("subscribers",  [list],     required=True)
        ]

# This config class represents the required data to be sent to Taskmaster when an
# event is posted from across the internet (via the oracle).
class TaskmasterEventPostConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("data",         [dict],     required=False,     default=None)
        ]


# =============================== Event Class ================================ #
class TaskmasterEvent:
    # Constructor. Takes in JSON data and parses it as a TaskmasterEventConfig.
    def __init__(self, jdata: dict):
        self.config = TaskmasterEventConfig()
        self.config.parse_json(jdata)

        # iterate through each subscriber and make sure the file exists. Also,
        # import each subscriber's code
        self.subscribers = []
        for sdata in self.config.subscribers:
            # parse each entry as a subscriber object
            sub = TaskmasterSubscriber(sdata)
            self.subscribers.append(sub)

    # String representation.
    def __str__(self):
        return "%s: %d subscriber(s)" % (self.config.name, len(self.config.subscribers))
    
    # Fires the event, which executes all subscribers. Takes in an optional data
    # parameter. Returns a dictionary of stdout/stderr results corresponding to
    # each executed subscriber.
    async def fire(self, data=None, stdout_fd=None, stderr_fd=None):
        # spawn each of the subscribers' child processes
        for sub in self.subscribers:
            sub.spawn(data=data, stdout_fd=stdout_fd, stderr_fd=stderr_fd)

