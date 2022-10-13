# This module defines the overarching Service class. A service performs some
# job that I want done automatically on my home server. Each service spins up
# one main thread to perform its job. Optionally, the service has an 'oracle'
#
#   Connor Shugg

# Imports
import os
import sys
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config
import lib.oracle


# ================================ Main Class ================================ #
# Main service class. Caller must 'start()' the service as if it was starting a
# thread. In addition, the caller must handle the function invocation to spawn
# the service's oracle (server) thread.
# Each service has a 'bank' that's used to store values that can be written to
# or read from by other threads synchronously.
class Service(threading.Thread):
    # Constructor.
    def __init__(self, config_path):
        threading.Thread.__init__(self, target=self.run)
        self.config = lib.config.Config(config_path)
        self.oracle = lib.oracle.Oracle(self)
        self.lock = threading.Lock()
        self.log_stream = sys.stdout
    
    # The service's main thread. This function must is where all the service's
    # actual work will occur, and thus must be extended by subclasses.
    def run(self):
        self.log("spawned.")

    # Takes in a message and writes it to the log's output, with a prefix.
    # Optionally takes in a 'stream' variable that accepts an optional file
    # pointer. Each time 'stream' is specified, an internal field is updated.
    # So, it only needs to be specified once to write to the same file stream
    # every time.
    def log(self, msg, stream=sys.stdout):
        self.log_stream = stream
        tid = threading.get_ident()
        prefix = "[%s-%d] " % (self.config.name, tid)
        self.log_stream.write("%s%s\n" % (prefix, msg))

