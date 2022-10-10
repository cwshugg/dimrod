#!/usr/bin/python3
# An example service class.

# Imports
from lib.service import Service


# ============================== Service Class =============================== #
class ExampleService(Service):
    # Constructor.
    def __init__(self):
        super().__init__("./config/example.json")
    
    # Starts the service's threads.
    def start(self):
        self.spawn_worker()
        self.spawn_watcher()


# =============================== Runner Code ================================ #
es = ExampleService()
es.start()

