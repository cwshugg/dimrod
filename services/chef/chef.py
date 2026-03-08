#!/usr/bin/python3
# My cooking/baking/etc. recipe management service.
# The goal of this service is to allow the user to store recipes in a
# structured format and query them via the service's oracle.

# Imports
import os
import sys
import json
import flask
import time
import re
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.cli import ServiceCLI

# Service imports
from recipe import Recipe


class ChefConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("recipe_dir",           [str],  required=True),
            ConfigField("recipe_refresh_rate",  [int],  required=False, default=180),
        ]


# ============================== Service Class =============================== #
class ChefService(Service):
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ChefConfig()
        self.config.parse_file(config_path)

        self.recipes = {}

    # Overridden main function implementation.
    def run(self):
        super().run()

        # If the recipe directory doesn't exist, make it
        if not os.path.isdir(self.config.recipe_dir):
            self.log.write("Recipe directory (%s) doesn't exist. Creating..." %
                           self.config.recipe_dir)
            os.mkdir(self.config.recipe_dir)

        previous_recipes_len = len(self.recipes)
        while True:
            # Load all recipes from the recipe directory; cache them in memory
            # for quick access.
            self.recipes = self.load_all_recipes()
            recipes_len = len(self.recipes)

            # Log the number of recipes we loaded, if the number changed since
            # the last time:
            if recipes_len != previous_recipes_len:
                self.log.write(
                    "Loaded %d recipe(s) from the recipe directory (%s from %d)." % (
                        recipes_len,
                        "up" if recipes_len > previous_recipes_len else "down",
                        previous_recipes_len
                    )
                )
            previous_recipes_len = recipes_len

            # Sleep for a short time before looping again:
            time.sleep(self.config.recipe_refresh_rate)

    # Returns the recipe object with the specified ID, or None if no such
    # recipe exists.
    def get_recipe_by_id(self, r_id: str):
        r_id = r_id.strip().lower()
        return self.recipes.get(r_id, None)

    # ---------------------------- Recipe Loading ---------------------------- #
    # Iterates through the configured recipe directory and loads all recipes that
    # were found. Returns a dictionary mapping recipe IDs to recipe objects.
    def load_all_recipes(self):
        all_recipes = {}
        for (root, dirs, files) in os.walk(self.config.recipe_dir):
            for f in files:
                # Skip all non-JSON files:
                if not f.lower().endswith(".json"):
                    continue

                # Load the JSON file and parse it as one (or multiple) recipes.
                recipes_from_file = {}
                try:
                    fpath = os.path.join(root, f)
                    recipes_from_file = self.load_recipes_from_file(fpath)
                except Exception as e:
                    self.log.write(
                        "Failed to load recipes from JSON file \"%s\": \"%s\". Skipping." %
                        (f, e)
                    )
                    continue

                # Iterate through the recipes that were loaded, and add them to
                # the overall dictionary. If any duplicates are found, throw an
                # error.
                for r_id, r in recipes_from_file.items():
                    if r_id in all_recipes:
                        raise Exception(
                            "Duplicate recipe ID \"%s\" found in file \"%s\". " \
                            "This ID is already used by another recipe.",
                            (r_id, f)
                        )
                    all_recipes[r_id] = r

        return all_recipes

    # Attempts to load one or more recipes from a single JSON file.
    # A dictionary of recipe objects, keyed by recipe ID string, is returned on
    # success.
    def load_recipes_from_file(self, fpath: str):
        recipes = {}
        with open(fpath, "r") as fp:
            jdata = json.load(fp)

            # Create a list of entries to parse, based on whether the JSON data
            # is a single object or a list of objects.
            entries = []
            if isinstance(jdata, dict):
                entries = [jdata]
            elif isinstance(jdata, list):
                entries = jdata

            # Iterate through the entries and attempt to parse as recipes.
            for entry in entries:
                r = Recipe.from_json(entry)

                # Do we already have a recipe with the same ID as this recipe?
                # If so, throw an error.
                if r.id in recipes:
                    raise Exception(
                        "Duplicate recipe ID \"%s\" found in file \"%s\"." %
                        (r.id, os.path.basename(fpath))
                    )

                # Otherwise, add this recipe to the dictionary:
                recipes[r.id] = r
        return recipes


# ============================== Service Oracle ============================== #
class ChefOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # Searches for a reminder by ID.
        @self.server.route("/recipes/get_by_id", methods=["POST"])
        def endpoint_recipes_get_by_id():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # Look for a recipe ID specified by the caller
            r_id = flask.g.jdata.get("id", None)
            if not r_id:
                return self.make_response(msg="Missing required field \"id\" in JSON data.",
                                          success=False, rstatus=400)

            # Query the recipe by ID. If no recipe is found, return an error.
            recipe = self.service.get_recipe_by_id(r_id)
            if not recipe:
                return self.make_response(msg="No recipe found with ID \"%s\"." % r_id,
                                          success=False, rstatus=404)

            return self.make_response(payload=recipe.to_json())

        # Returns basic information about all recipes.
        @self.server.route("/recipes/list_all", methods=["POST"])
        def endpoint_recipes_get_by_id():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # Iterate through every recipe and extract basic information; build
            # a dictionary of recipes, keyed by recipe ID.
            result = {}
            for r_id, recipe in self.service.recipes.items():
                result[r_id] = {
                    "id": recipe.id,
                    "title": recipe.title,
                    "description": recipe.description,
                }
            return self.make_response(payload=result)

        # Returns full recipe objects for every recipe.
        @self.server.route("/recipes/get_all", methods=["POST"])
        def endpoint_recipes_get_by_id():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # Iterate through every recipe and convert each to JSON; build a
            # dictionary of recipes, keyed by recipe ID.
            result = {}
            for r_id, recipe in self.service.recipes.items():
                result[r_id] = recipe.to_json()
            return self.make_response(payload=result)


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=ChefConfig, service=ChefService, oracle=ChefOracle)
    cli.run()

