# This module defines the event Subscriber class. It represents a small program
# that is invoked when an event is fired off.

# Imports
import os
import sys
import json
import subprocess

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ============================ Subscriber Config ============================= #
class TaskmasterSubscriberConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",             [str],      required=True),
            ConfigField("executable",       [str],      required=True)
        ]


# ============================= Subscriber Class ============================= #
class TaskmasterSubscriber:
    # Constructor. Takes in JSON data and parses it as a subscriber config.
    def __init__(self, jdata: dict):
        self.config = TaskmasterSubscriberConfig()
        self.config.parse_json(jdata)

        # make sure the executable file exists
        assert os.path.isfile(self.config.executable), \
               "could not find executable file: %s" % self.config.executable
        
    # Spawns a child process and has it execute the executable file. Takes in an
    # optional data parameter.
    def spawn(self, data=None):
        args = [self.config.executable]
        if data is not None:
            # add a single argument containing all data as a JSON string
            args.append(json.dumps(data))
        process = subprocess.Popen(args, stdout=None, stderr=None)
        return process

