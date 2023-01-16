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
from lists import ScriboList, ScriboListItem

# Globals
default_list_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ldbs")


# =============================== Config Class =============================== #
class ScriboConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("list_dir",         [str],      required=False,     default=default_list_dir),
            ConfigField("list_extension",   [str],      required=False,     default="db")
        ]


# ============================== Service Class =============================== #
class ScriboService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ScriboConfig()
        self.config.parse_file(config_path)

        # given the list directory, create it if it doesn't exist
        if not os.path.isdir(self.config.list_dir):
            os.mkdir(self.config.list_dir)

    # Overridden main function implementation.
    def run(self):
        super().run()

    # ------------------------------- Helpers -------------------------------- #
    # Converts a file path to a list name.
    def file_to_name(self, path: str):
        file = os.path.basename(path).lower().replace(" ", "_")
        return file.replace(".%s" % self.config.list_extension, "")
    
    # Converts a list name to a file path.
    def name_to_file(self, name: str):
        path = os.path.join(self.config.list_dir, name.lower().replace(" ", "_"))
        return path + ".%s" % self.config.list_extension

    # ------------------------------- List API ------------------------------- #
    # Returns an array of all currently-stored list paths.
    def list_get_paths(self):
        # iterate through the list directory and find all list databases
        paths = []
        ext = ".%s" % self.config.list_extension
        for (root, dirs, files) in os.walk(self.config.list_dir):
            for f in files:
                if f.endswith(ext):
                    paths.append(os.path.join(root, f))
        return paths
    
    # Returns a single list given a matching name. Returns None if no match is
    # found.
    def list_get(self, name: str):
        name = name.lower().replace(" ", "_")
        paths = self.list_get_paths()
        for f in paths:
            print("DOES '%s' == '%s'?" % (name, self.file_to_name(f)))
            if name == self.file_to_name(f):
                return ScriboList(f)
        return None
    
    # Creates a new list, given the name. Returns the new list.
    def list_create(self, name: str):
        # first, make sure the list doesn't already exist
        assert self.list_get(name) is None, \
               "a list already exists with the name \"%s\"" % name
        # if the list doesn't exist, we'll create a new ScriboList object (it
        # will initialize automatically)
        l = ScriboList(self.name_to_file(name))
        return l
    
    # Deletes a list, given the name.
    def list_delete(self, name: str):
        # find the list, and complain if it doesn't exist
        l = self.list_get(name)
        assert l is not None, "unable to find list with name \"%s\"" % name

        # delete the database file that stores the list's items
        os.remove(self.name_to_file(name))


# ============================== Service Oracle ============================== #
class ScriboOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Retrieves and returns all lists.
        @self.server.route("/list/get/all", methods=["GET"])
        def endpoint_list_get_all():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
    
        # Retrieves and returns a single list.
        @self.server.route("/list/get", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
        
        # Creates a new list.
        @self.server.route("/list/create", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
        
        # Deletes an existing list.
        @self.server.route("/list/delete", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
        
        # Adds an entry to an existing list.
        @self.server.route("/list/append", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
        
        # Removes an entry from an existing list.
        @self.server.route("/list/remove", methods=["POST"])
        def endpoint_list_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response("Missing JSON data.",
                                          success=False, rstatus=400)
            
            # TODO
            pass
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=ScriboConfig, service=ScriboService, oracle=ScriboOracle)
cli.run()

