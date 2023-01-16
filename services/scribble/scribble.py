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
from lists import ScribbleList, ScribbleListItem

# Globals
default_list_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ldbs")


# =============================== Config Class =============================== #
class ScribbleConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("list_dir",         [str],      required=False,     default=default_list_dir),
        ]


# ============================== Service Class =============================== #
class ScribbleService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ScribbleConfig()
        self.config.parse_file(config_path)

        # given the list directory, create it if it doesn't exist
        if not os.path.isdir(self.config.list_dir):
            os.mkdir(self.config.list_dir)

    # Overridden main function implementation.
    def run(self):
        super().run()

    # ------------------------------- List API ------------------------------- #
    # Returns an array of all currently-stored list paths.
    def list_get_paths(self):
        # iterate through the list directory and find all list databases
        paths = []
        for (root, dirs, files) in os.walk(self.config.list_dir):
            for f in files:
                if f.endswith(".db"):
                    paths.append(os.path.join(root, f))
        return paths
    
    # Returns a single list given a matching name. Returns None if no match is
    # found.
    def list_get(self, name: str):
        name = name.lower().replace(" ", "_")
        paths = self.list_get_paths()
        for f in paths:
            if name == ScribbleList.file_to_name(f):
                return ScribbleList(f)
        return None
    
    # Creates a new list, given the name. Returns the new list.
    def list_create(self, name: str):
        # first, make sure the list doesn't already exist
        assert self.list_get(name) is None, \
               "a list already exists with the name \"%s\"" % name
        # if the list doesn't exist, we'll create a new ScribbleList object (it
        # will initialize automatically)
        l = ScribbleList(ScribbleList.name_to_file(name, self.config.list_dir))
        return l
    
    # Deletes a list, given the name.
    def list_delete(self, l: ScribbleList):
        os.remove(l.path)


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
            
            # get all list paths from the service and load each one in, adding
            # them to a list
            result = []
            for path in self.service.list_get_paths():
                l = ScribbleList(path)
                result.append(l.to_json())
            return self.make_response(payload=result)
    
        # Retrieves and returns a single list.
        @self.server.route("/list/get", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # the user should have passed in a list name
            if "name" not in flask.g.jdata:
                return self.make_response(msg="Missing list name.",
                                          success=False, rstatus=400)
            name = flask.g.jdata["name"]

            # use the name to search for a list
            l = self.service.list_get(name)
            if l is None:
                return self.make_response(msg="No matching list found.",
                                          success=False)
            else:
                return self.make_response(payload=l.to_json())
        
        # Creates a new list.
        @self.server.route("/list/create", methods=["POST"])
        def endpoint_list_create():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # the user should have passed in a list name
            if "name" not in flask.g.jdata:
                return self.make_response(msg="Missing list name.",
                                          success=False, rstatus=400)
            name = flask.g.jdata["name"]

            # use the name to create a new list
            try:
                l = self.service.list_create(name)
                return self.make_response(msg="Successfully created new list \"%s\"" % name)
            except Exception as e:
                return self.make_response(msg="Failed to make a new list: %s" % e,
                                          success=False)
        
        # Deletes an existing list.
        @self.server.route("/list/delete", methods=["POST"])
        def endpoint_list_delete():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # the user should have passed in a list name
            if "name" not in flask.g.jdata:
                return self.make_response(msg="Missing list name.",
                                          success=False, rstatus=400)
            name = flask.g.jdata["name"]

            # use the name to find the list
            l = self.service.list_get(name)
            if l is None:
                return self.make_response(msg="No matching list found.",
                                          success=False)

            # now, pass the list to the service for deletion
            try:
                self.service.list_delete(l)
                return self.make_response(msg="List deleted successfully.")
            except Exception as e:
                return self.make_response(msg="Failed to delete list: %s" % e,
                                          success=False)

        
        # Adds an entry to an existing list.
        @self.server.route("/list/append", methods=["POST"])
        def endpoint_list_append():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # the user should have passed in a list name and item string
            if "name" not in flask.g.jdata or "item" not in flask.g.jdata:
                return self.make_response(msg="Missing list fields.",
                                          success=False, rstatus=400)
            name = flask.g.jdata["name"]
            text = flask.g.jdata["item"]
            
            # use the name to search for a list
            l = self.service.list_get(name)
            if l is None:
                return self.make_response(msg="No matching list found.",
                                          success=False)

            # add the item to the list
            i = ScribbleListItem(text)
            try:
                l.add(i)
                return self.make_response(msg="Successfully added \"%s\" to list \"%s\"." % (text, name))
            except Exception as e:
                return self.make_response(msg="Failed to add item to list \"%s\": %s" % (name, e),
                                          success=False)
                                              
 
        # Removes an entry from an existing list.
        @self.server.route("/list/remove", methods=["POST"])
        def endpoint_list_remove():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
            # the user should have passed in a list name and item ID
            if "name" not in flask.g.jdata or "iid" not in flask.g.jdata:
                return self.make_response(msg="Missing JSON fields.",
                                          success=False, rstatus=400)
            name = flask.g.jdata["name"]
            iid = flask.g.jdata["iid"]
            
            # use the name to search for a list
            l = self.service.list_get(name)
            if l is None:
                return self.make_response(msg="No matching list found.",
                                          success=False)

            # make a dummy list item, with the same ID number to use for
            # deleting the existing one in the database
            i = ScribbleListItem("", iid=iid)
            try:
                l.remove(i)
                return self.make_response(msg="Successfully removed from list \"%s\"." % name)
            except Exception as e:
                return self.make_response(msg="Failed to remove: %s" % e,
                                          success=False)
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=ScribbleConfig, service=ScribbleService, oracle=ScribbleOracle)
cli.run()

