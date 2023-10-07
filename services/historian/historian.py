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
from event import HistorianEvent
from db import HistorianDatabase


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

        # create a database object
        self.db = HistorianDatabase(self.config.db_path)
        
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
            
            # look for JSON specifying the number of entries to return
            if "count" not in flask.g.jdata or type(flask.g.jdata["count"]) != int:
                return self.make_response(msg="Must specify 'count' integer.",
                                          success=False, rstatus=400)
            count = flask.g.jdata["count"]

            # search the database
            results = self.service.db.search(count=count)
            payload = []
            for e in results:
                payload.append(e.to_json(include_id=True))
            return self.make_response(success=True, payload=payload)
        
        # Endpoint that retrieves and returns an event with the given ID.
        @self.server.route("/retrieve/by_id", methods=["POST"])
        def endpoint_retrieve_by_id():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # look for the ID string in the json data
            if "event_id" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'event_id' string.",
                                          success=False, rstatus=400)
            eid = str(flask.g.jdata["event_id"])

            # search the database
            result = self.service.db.search_by_id(eid)
            if result is None:
                return self.make_response(msg="Couldn't find a matching event.",
                                          success=False)
            else:
                pyld = result.to_json(include_id=True)
                return self.make_response(success=True, payload=pyld)
            
        # Endpoint used to submit a single event to the historian.
        @self.server.route("/submit", methods=["POST"])
        def endpoint_submit():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # attempt to convert the JSON to an event object and add it to the
            # database
            try:
                e = HistorianEvent()
                e.parse_json(flask.g.jdata)
                self.service.db.add(e)
                return self.make_response(msg="Added successfully.",
                                          success=True)
            except Exception as e:
                raise e # DEBUGGING
                return self.make_response(msg="Invalid event data: %s" % e,
                                          success=False, rstatus=400)


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=HistorianConfig, service=HistorianService, oracle=HistorianOracle)
cli.run()

