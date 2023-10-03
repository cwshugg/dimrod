#!/usr/bin/python3
# This service is responsible for chronicling all events that happen across all
# services. Other services, if they wish to store some event, must reach out to
# the historian's oracle and submit an event to be stored.
#
# Storing these events will be useful for debugging, among other things.
# Eventually, it could be interesting to use these events to serve as a "memory"
# of sorts for DImROD, such that, when the user is talking to DImROD, it can
# remember what happened in the past.

# Imports
import os
import sys
import flask
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Service imports
# TODO


# =============================== Config Class =============================== #
class HistorianConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        fields = [
            ConfigField("db_path",              [str],      required=False,     default="./.history.db"),
        ]
        self.fields += fields


# ================================= Service ================================== #
class HistorianService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = HistorianConfig()
        self.config.parse_file(config_path)
        
    # Overridden main function implementation.
    def run(self):
        super().run()


# ============================== Service Oracle ============================== #
class HistorianOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint that retrieves and returns the latest N events.
        @self.server.route("/retrieve/latest", methods=["POST"])
        def endpoint_retrieve_latest():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO - get fields from JSON data and return data
            #   - number_to_retrieve
            #   - submitter_name
            #   - category name
            #   - etc.
            return self.make_response(success=False, msg="NOT YET IMPLEMENTED")
        
        # Endpoint used to submit a single event to the historian.
        @self.server.route("/submit", methods=["POST"])
        def endpoint_submit():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO - get data, convert to event object, and add to DB
            return self.make_response(success=False, msg="NOT YET IMPLEMENTED")


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=HistorianConfig, service=HistorianService, oracle=HistorianOracle)
cli.run()

