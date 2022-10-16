# This module defines the overarching Service class. A service performs some
# job that I want done automatically on my home server. Each service spins up
# one main thread to perform its job.
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
import lib.log


# ============================== Service Config ============================== #
# A config class for a generic service.
class ServiceConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("service_name",  [str],      required=True),
            lib.config.ConfigField("service_log",   [str],      required=False)
        ]


# ================================ Main Class ================================ #
# Main service class. Caller must 'start()' the service as if it was starting a
# thread.
class Service(threading.Thread):
    # Constructor.
    def __init__(self, config_path):
        threading.Thread.__init__(self, target=self.run)
        self.config = ServiceConfig()
        self.config.parse_file(config_path)
        self.lock = threading.Lock()

        # examine the config for a log stream
        log_file = sys.stdout
        if self.config.service_log:
            log_file = self.config.service_log
        self.log = lib.log.Log(self.config.service_name, stream=log_file)
    
    # The service's main thread. This function must is where all the service's
    # actual work will occur, and thus must be extended by subclasses.
    def run(self):
        self.log.write("running main thread.")

