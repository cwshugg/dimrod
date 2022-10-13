#!/usr/bin/python3
# A super simple service that acts as an example.

# Imports
import os
import sys
import json
import flask

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.service import Service
from lib.oracle import Oracle


# ============================== Service Class =============================== #
class HelloService(Service):
    # Constructor.
    def __init__(self):
        super().__init__(os.path.join(pdir, "config/example.json"))
    
    # Overridden abstract class implementation for the service thread.
    def run(self):
        super().run()


# ============================== Service Oracle ============================== #
class HelloOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint for a simple greeting.
        @self.server.route("/hello")
        def endpoint_hello():
            return self.make_response(msg="Hello!")

        # Endpoint for a goodbye.
        @self.server.route("/goodbye")
        def endpoint_goodbye():
            return self.make_response(msg="Goodbye!")
        
        # JSON tests endpoint.
        @self.server.route("/json", methods=["GET", "POST"])
        def endpoint_json():
            if not flask.g.jdata:
                return self.make_response(msg="No JSON data provided.")
            # if JSON data was given, send it back
            jmsg = json.dumps(flask.g.jdata, indent=4)
            return self.make_response(msg=jmsg)


# =============================== Runner Code ================================ #
hs = HelloService()
ho = HelloOracle(hs)
hs.start()
ho.start()
ho.join()
hs.join()

