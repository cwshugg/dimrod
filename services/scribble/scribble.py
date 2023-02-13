#!/usr/bin/python3
# My list-keeping service. Used to create and store lists. Useful for todo
# lists, packing lists, etc.

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

# Service imports
from ticktick import TickTickAPI


# =============================== Config Class =============================== #
class ScribbleConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("ticktick_auth_username",       [str],  required=True),
            ConfigField("ticktick_auth_password",       [str],  required=True),
            ConfigField("ticktick_refresh_threshold",   [int],  required=False,     default=60)
        ]


# ============================== Service Class =============================== #
class ScribbleService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ScribbleConfig()
        self.config.parse_file(config_path)

        # set up a ticktick API client and attempt to log in
        self.ticktick = TickTickAPI(self.config.ticktick_auth_username,
                                    self.config.ticktick_auth_password,
                                    refresh_threshold=self.config.ticktick_refresh_threshold)
        self.ticktick.refresh()

    # Overridden main function implementation.
    def run(self):
        super().run()


# ============================== Service Oracle ============================== #
class ScribbleOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Retrieves and returns all lists.
        @self.server.route("/list/get/all", methods=["GET"])
        def endpoint_list_get_all():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # retrieve *everything* from the ticktick API
            projects = self.service.ticktick.get_projects()
            result = []
            for pid in projects:
                pjson = projects[pid].to_json()
                result.append(pjson)

            return self.make_response(payload=result)
        
        # Retrieves and returns a single list.
        @self.server.route("/list/get", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # make sure an ID was given
            if "id" not in flask.g.jdata:
                return self.make_response(msg="Missing ID string.",
                                          success=False, rstatus=400)
            list_id = str(flask.g.jdata["id"])

            # ping the API for the project
            p = self.ticktick.get_project(list_id)
            if p is None:
                return self.make_response(msg="Unknown ID string.",
                                          success=False, rstatus=400)

            return self.make_response(payload=p.to_json())
            
        # Adds an entry to an existing list.
        @self.server.route("/list/append", methods=["POST"])
        def endpoint_list_append():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            return self.make_response(msg="TODO")
            
        # Removes an entry from an existing list.
        @self.server.route("/list/remove", methods=["POST"])
        def endpoint_list_remove():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            return self.make_response(msg="TODO")
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=ScribbleConfig, service=ScribbleService, oracle=ScribbleOracle)
cli.run()

