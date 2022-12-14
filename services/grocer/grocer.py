#!/usr/bin/python3
# My grocery list service. Used to construct grocery lists for weekly shopping.
#
# It would be nice to use Google Maps' API to get info on nearby grocery stores.
#   https://developers.google.com/maps/documentation/javascript/places

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
from glist import GrocerList


# =============================== Config Class =============================== #
class GrocerConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        fields = [
            ConfigField("stores",           [list],     required=True)
        ]
        self.fields += fields


# ============================== Service Class =============================== #
class GrocerService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = GrocerConfig()
        self.config.parse_file(config_path)
        self.lists = []

    # Overridden main function implementation.
    def run(self):
        super().run()
        # TODO

    # ---------------------------- Grocery Lists ----------------------------- #
    # Takes in a grocery list and adds it to the service.
    def add_list(self, glist: GrocerList):
        self.lists.append(glist)
    
    # Takes in a grocery list and deletes it.
    def remove_list(self, glist: GrocerList):
        # TODO
        pass
    
    # Takes in a grocery list ID and finds the matching GrocerList object.
    # The object is returned, or None is returned if it can't be found.
    def get_list(self, glid: str):
        # TODO
        return None
    

# ============================== Service Oracle ============================== #
class GrocerOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint that retrieves all lists from the service.
        @self.server.route("/lists", methods=["GET"])
        def endpoint_lists():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            result = []
            # TODO
            return self.make_response(success=True, payload=result)
       
        # ------------------------ List Manipulation ------------------------- #
        # Creates a new list.
        @self.server.route("/lists/create", methods=["POST"])
        def endpoint_list_create():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing information.",
                                          success=False, rstatus=400)
            
            # TODO
            return self.make_response(success=True)
        
        # Deletes an existing grocery list.
        @self.server.route("/lists/delete", methods=["POST"])
        def endpoint_list_delete():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing information.",
                                          success=False, rstatus=400)
            
            # TODO
            return self.make_response(success=True)
        
        # Adds a new item to a specific list.
        @self.server.route("/lists/add", methods=["POST"])
        def endpoint_list_add():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing information.",
                                          success=False, rstatus=400)

            # TODO
            return self.make_response(success=True)

        # Removes an item from a specific list.
        @self.server.route("/lists/remove", methods=["POST"])
        def endpoint_list_remove():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing information.",
                                          success=False, rstatus=400)

            # TODO
            return self.make_response(success=True)


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=GrocerConfig, service=GrocerService, oracle=GrocerOracle)
cli.run()

