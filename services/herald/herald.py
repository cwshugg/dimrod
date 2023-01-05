#!/usr/bin/python3
# The Herald is the ONLY service allowed access to the internet. It's job is to
# receive commands from my internet-connected devices and issue commands to
# the other services.

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
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI


# =============================== Config Class =============================== #
class HeraldConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("herald_commands",  [list], required=True)
        ]


# ============================== Service Class =============================== #
class HeraldService(Service):
    # Overridden abstract class implementation for the service thread.
    def run(self):
        super().run()


# ============================== Service Oracle ============================== #
class HeraldOracle(Oracle):
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
cli = ServiceCLI(config=HeraldConfig, service=HeraldService, oracle=HeraldOracle)
cli.run()

