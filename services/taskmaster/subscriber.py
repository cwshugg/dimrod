# This module defines the event Subscriber class. It represents a small program
# that is invoked when an event is fired off.

# Imports
import os
import sys
import json
import subprocess
import asyncio

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
    # optional data parameter, and optional file descriptors to which stdout and
    # stderr should be written.
    def spawn(self, data=None, stdout_fd=None, stderr_fd=None):
        # Helper function that spawns, then waits, for the subprocess to finish.
        # Upon completion, STDOUT and STDERR are written to the given locations.
        async def spawn_and_wait(argv):
            process = subprocess.Popen(argv,
                                       stdout=None if stdout_fd is None else subprocess.PIPE,
                                       stderr=None if stdout_fd is None else subprocess.PIPE)
            (stdout, stderr) = process.communicate()
            for line in stdout.decode().split("\n"):
                if len(line) > 0:
                    stdout_fd.write("[STDOUT] %s\n" % line)
            for line in stderr.decode().split("\n"):
                if len(line) > 0:
                    stdout_fd.write("[STDERR] %s\n" % line)
        
        # runner code - build an argument list, then invoke spawn_and_wait
        args = [self.config.executable]
        if data is not None:
            # add a single argument containing all data as a JSON string
            args.append(json.dumps(data))
        asyncio.run(spawn_and_wait(args))

