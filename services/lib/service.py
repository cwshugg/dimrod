# This module defines the overarching Service class, that encompasses the Flask
# server. Each service has at least two threads:
#   1. One thread to run the Flask server.
#   2. Another thread to run the main service code.
#
#   Connor Shugg

# Imports
import os
import sys
import threading
import abc

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config
from lib.server import server


# ================================ Main Class ================================ #
# Main service class
class Service:
    # Constructor.
    def __init__(self, config_path):
        self.config = Config(config_path)
        self.worker = ServiceWorker(self)
        self.watcher = ServiceWatcher(self)
    
    # Runs the service's main worker thread.
    def spawn_worker(self):
        self.worker.start()
    
    # Runs the service's watcher thread (which, in turn, launches a HTTP server
    # used to interface with the service).
    def spawn_watcher(self):
        self.watcher.start()


# ============================== Worker Thread =============================== #
# Service "worker" thread that does the service's actual work.
class ServiceWorker(threading.Thread, abc.ABC):
    # Constructor.
    def __init__(self, service):
        super().__init__(self, target=self.run)
        self.service = service

    # Thread main function. Must be implemented by individual services.
    @abc.abstractmethod
    def run(self):
        pass


# ============================== Server Thread =============================== #
# Service "watcher" thread that runs a flask server to act as a middleman
# between the user and the server.
class ServiceWatcher(threading.Thread):
    # Constructor.
    def __init__(self, service):
        super().__init__(self, target=self.run)
        self.service = service

    # Thread main function. Launches the servier.
    def run(self):
        # set needed server configuration fields, then launch the server
        server.config["service"] = self.service
        server.run(config.server_addr, port=config.server_port)        

