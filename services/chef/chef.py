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
from lib.nla import NLAEndpoint, NLAEndpointInvokeParameters, NLAResult
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface

# Service imports
from recipe import Recipe


class ChefConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("recipe_dir",           [str],  required=True),
            ConfigField("dialogue",             [DialogueConfig], required=True),
            ConfigField("recipe_refresh_rate",  [int],  required=False, default=180),
            ConfigField("resolve_recipe_dialogue_retries", [int], required=False, default=4),
            ConfigField("resolve_recipe_default_servings_needed", [int], required=False, default=2),
        ]


# ============================== Service Class =============================== #
class ChefService(Service):
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ChefConfig()
        self.config.parse_file(config_path)

        self.recipes = {}

    def run(self):
        """Overridden main function implementation."""
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
            try:
                self.recipes = self.load_all_recipes()
            except Exception as e:
                self.log.write("Failed to load recipes: \"%s\". Will retry on the next refresh." % e)
                self.recipes = {}
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

    def get_recipe_by_id(self, r_id: str):
        """Returns the recipe object with the specified ID, or None if no such
        recipe exists.
        """
        r_id = r_id.strip().lower()
        return self.recipes.get(r_id, None)

    # ---------------------------- Recipe Loading ---------------------------- #
    def load_all_recipes(self):
        """Iterates through the configured recipe directory and loads all recipes that
        were found. Returns a dictionary mapping recipe IDs to recipe objects.
        """
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
                            "This ID is already used by another recipe." %
                            (r_id, f)
                        )
                    all_recipes[r_id] = r

        return all_recipes

    def load_recipes_from_file(self, fpath: str):
        """Attempts to load one or more recipes from a single JSON file.

        A dictionary of recipe objects, keyed by recipe ID string, is returned on
        success.
        """
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

    def resolve_recipe(self, text: str):
        """Uses an LLM to resolve the provided text to one of the recipes saved in
        the chef service. Returns None, or the matching recipe object.
        """
        # Does the text contain the ID or name of any of the recipes? If so,
        # we'll use the recipe ID to inject a hint into the LLM prompt to steer
        # it in the right direction.
        hint_recipe_id = None
        for r_id, recipe in self.recipes.items():
            if r_id in text.lower():
                hint_recipe_id = r_id
                break
            if recipe.title is not None and \
               recipe.title.lower() in text.lower():
                hint_recipe_id = r_id
                break

        # Build a prompt for the LLM; include all information about recipes:
        prompt_intro = "You are a helpful assistant for a cooking recipe management service. " \
                       "Your task is to determine which of the available recipes best matches a user's request. " \
                       "These recipes are:\n\n"
        prompt_intro += "[\n"
        for r_id, recipe in self.recipes.items():
            prompt_intro += "    %s,\n" % json.dumps({
                "id": recipe.id,
                "title": recipe.title,
                "description": recipe.description,
                "servings": recipe.servings,
            })
        prompt_intro += "]\n\n"

        # Explain the output format:
        output_format = {
            "id": "<the ID of the recipe that matches the user's request>",
            "quantity": "<the number of copies of the recipe the user wants to make; default is 1 if not specified>",
        }
        prompt_intro += "\nYou will be provided with a string of text. " \
                        "Please examine this string and return the following JSON object:\n\n"
        prompt_intro += json.dumps(output_format, indent=4) + "\n\n"
        prompt_intro += "If the user's request does not match any of the available recipes, return an empty JSON object ({}).\n" \
                        "Use the recipe's serving amount as a to determine how mnay copies of the recipe the user wants. " \
                        "The \"quantity\" field should be set such that the number of servings in the recipe multiplied by the quantity is equal to the number of servings the user wants. (Or *greater than* if it doesn't multiple evenly.) " \
                        "For example, if the user's request indicates they want to make a recipe (with \"servings\" = 2) for six people, the \"quantity\" field in your response should be set to 3."
        prompt_intro += "You can assume that for a single meal, %d servings are needed." % \
                        self.config.resolve_recipe_default_servings_needed

        # Build content to pass in after the intro prompt:
        prompt_content = "%s" % text
        if hint_recipe_id is not None:
            prompt_content += " (%s)" % hint_recipe_id

        # Set up a dialogue interface to use the LLM, and attempt to resolve the
        # user's request.
        dialogue = DialogueInterface(self.config.dialogue)
        fail_count = 0
        recipe_info = None
        for attempt in range(self.config.resolve_recipe_dialogue_retries):
            try:
                r = dialogue.oneshot(prompt_intro, prompt_content)
                recipe_info = json.loads(r)

                # If the LLM returned an empty object, this means it didn't find
                # a match for the user's request. We can return early in this
                # case.
                if recipe_info == {}:
                    return None

                # Otherwise, make sure the LLM returned the required fields:
                if "id" not in recipe_info:
                    raise Exception("LLM response is missing required field \"id\".")
                if "quantity" not in recipe_info:
                    recipe_info["quantity"] = 1

                # Does the ID string match one of our recipes? If not, this means the LLM returned an invalid recipe ID, so we'll throw an error.
                r_id = recipe_info["id"].strip().lower()
                if r_id not in self.recipes:
                    raise Exception(
                        "LLM response contains invalid recipe ID \"%s\" that doesn't match any known recipes." %
                        r_id
                    )

                # If all else succeeded, we can return the recipe info:
                return recipe_info
            except Exception as e:
                if fail_count == self.config.resolve_recipe_dialogue_retries:
                    raise e
                fail_count += 1
                continue

        return None

# ============================== Service Oracle ============================== #
class ChefOracle(Oracle):
    def endpoints(self):
        """Endpoint definition function."""
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
        def endpoint_recipes_list_all():
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
                    "links": recipe.links,
                }
            return self.make_response(payload=result)

        # Returns full recipe objects for every recipe.
        @self.server.route("/recipes/get_all", methods=["POST"])
        def endpoint_recipes_get_all():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # Iterate through every recipe and convert each to JSON; build a
            # dictionary of recipes, keyed by recipe ID.
            result = {}
            for r_id, recipe in self.service.recipes.items():
                result[r_id] = recipe.to_json()
            return self.make_response(payload=result)

        # Resolves the provided text as a recipe.
        @self.server.route("/recipes/resolve", methods=["POST"])
        def endpoint_recipes_resolve():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # Extract the text to resolve:
            text = flask.g.jdata.get("text", None)
            if not text:
                return self.make_response(msg="Missing required field \"text\" in JSON data.",
                                          success=False, rstatus=400)

            recipe_info = self.service.resolve_recipe(text)
            if recipe_info is None:
                return self.make_response(msg="No matching recipe found.",
                                          success=False, rstatus=404)

            return self.make_response(payload=recipe_info)

    def init_nla(self):
        """Registers NLA endpoints for recipe listing and lookup."""
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                "name": "list_recipes",
                "description": "List all available recipes with their names and IDs. "
                               "Phrases like \"what recipes do you know?\", "
                               "\"list all recipes\", \"what can I cook?\", etc."
            }).set_handler(nla_list_recipes),
            NLAEndpoint.from_json({
                "name": "get_recipe",
                "description": "Look up a specific recipe by name or description "
                               "and display its ingredients and steps. "
                               "Phrases like \"how do I make tacos?\", "
                               "\"what's in the chili mac recipe?\", "
                               "\"give me the enchilada recipe\", etc."
            }).set_handler(nla_get_recipe),
        ]


# =============================== NLA Helpers ================================ #
def _match_recipe_by_substring(user_text, recipes):
    """Attempts to match a recipe by checking if any recipe ID or title
    appears as a substring in the user text (case-insensitive).

    Returns the matched Recipe, or None if no match is found.
    """
    text_lower = user_text.lower()
    for r_id, recipe in recipes.items():
        if r_id in text_lower:
            return recipe
        if recipe.title is not None and recipe.title.lower() in text_lower:
            return recipe
    return None


def _format_ingredient(ingredient, quantity_multiplier=1):
    """Formats a single Ingredient object for plain-text display.

    Returns a string like '(1x) Ground beef' or '(2x) Elbow macaroni'.
    """
    quantity = ingredient.quantity * quantity_multiplier

    # Format quantity as integer if it is a whole number, otherwise as float.
    if quantity == int(quantity):
        quantity_str = str(int(quantity))
    else:
        quantity_str = str(quantity)

    title = ingredient.title if ingredient.title else ingredient.id
    optional_marker = " (optional)" if ingredient.is_optional else ""
    return "(%sx) %s%s" % (quantity_str, title, optional_marker)


# =============================== NLA Handlers =============================== #
def nla_list_recipes(oracle, jdata):
    """NLA handler that lists all available recipes with their titles and IDs."""
    params = NLAEndpointInvokeParameters.from_json(jdata)

    recipes = oracle.service.recipes
    if len(recipes) == 0:
        return NLAResult.from_json({
            "success": True,
            "message": "No recipes are currently available."
        })

    recipe_strs = []
    for r_id, recipe in sorted(recipes.items()):
        title = recipe.title if recipe.title else "(Untitled)"
        desc = "· %s [%s]" % (title, r_id)
        if recipe.description:
            desc += " - %s" % recipe.description
        recipe_strs.append(desc)

    msg = "Available recipes:\n" + "\n".join(recipe_strs)
    return NLAResult.from_json({
        "success": True,
        "message": msg
    })


def nla_get_recipe(oracle, jdata):
    """NLA handler that looks up a recipe by natural language and returns its
    full details including ingredients (with quantities) and numbered steps.

    Uses substring matching against recipe names/IDs first. If no match is
    found, falls back to LLM-based resolution via the service's
    resolve_recipe() method (which requires a dialogue config).
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    user_text = params.message
    if params.has_substring():
        user_text = params.substring

    recipes = oracle.service.recipes
    if len(recipes) == 0:
        return NLAResult.from_json({
            "success": False,
            "message": "No recipes are currently available."
        })

    # Tier 1: substring matching against recipe IDs and titles.
    recipe = _match_recipe_by_substring(user_text, recipes)
    quantity = 1

    # Tier 2: LLM fallback via resolve_recipe() (handles both matching and
    # quantity/servings calculation).
    if recipe is None:
        try:
            recipe_info = oracle.service.resolve_recipe(user_text)
            if recipe_info is not None:
                r_id = recipe_info.get("id", "").strip().lower()
                recipe = oracle.service.get_recipe_by_id(r_id)
                quantity = recipe_info.get("quantity", 1)
        except Exception:
            # LLM resolution failed; fall through to the error below.
            pass

    if recipe is None:
        names = ["· %s [%s]" % (r.title if r.title else r.id, r_id)
                 for r_id, r in sorted(recipes.items())]
        return NLAResult.from_json({
            "success": False,
            "message": "I could not find a matching recipe. "
                       "Available recipes:\n" + "\n".join(names)
        })

    # Build formatted output.
    title = recipe.title if recipe.title else recipe.id
    msg = "%s [%s]\n" % (title, recipe.id)

    if recipe.description:
        msg += "%s\n" % recipe.description

    if recipe.servings is not None:
        total_servings = recipe.servings * quantity
        if quantity > 1:
            msg += "Servings: %d (%dx recipe, %d servings each)\n" % (
                total_servings, quantity, recipe.servings
            )
        else:
            msg += "Servings: %d\n" % total_servings

    # Ingredients section.
    if recipe.ingredients and len(recipe.ingredients) > 0:
        msg += "\nIngredients:\n"
        for ing in recipe.ingredients:
            msg += "· %s\n" % _format_ingredient(ing, quantity)

    # Steps section (numbered).
    if recipe.steps and len(recipe.steps) > 0:
        msg += "\nSteps:\n"
        for i, step in enumerate(recipe.steps, start=1):
            step_text = step.description if step.description else \
                        (step.title if step.title else step.id)
            msg += "%d. %s\n" % (i, step_text)

    return NLAResult.from_json({
        "success": True,
        "message": msg,
        "message_context": "This is a recipe with ingredients and steps. "
                           "Present it clearly to the user. "
                           "Do not add or remove any ingredients or steps."
    })


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=ChefConfig, service=ChefService, oracle=ChefOracle)
    cli.run()

