# Imports
import os
import sys

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob, TaskConfig
from tasks.groceries.base import *
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession, OracleSessionConfig
from chef.recipe import Recipe, Ingredient, IngredientReplenishType

class TaskJob_Groceries_RecipeResolver(TaskJob_Groceries):
    """A taskjob that scans the grocery list for mention of recipe names.

    If a recipe name is found, the taskjob polls the chef service for the
    recipe's ingredients and adds them to the grocery list.
    """
    def __init__(self, service):
        super().__init__(service)
        self.refresh_rate = 120

        self.chef_config_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "chef_config.json"
        )
        self.chef_config = None

    def get_chef_session(self):
        """Initializes and returns an authenticated session with the chef service."""
        # Attempt to load the chef config file, if it hasn't been loaded yet.
        if self.chef_config is None:
            self.chef_config = OracleSessionConfig.from_file(self.chef_config_path)
        s = OracleSession(self.chef_config)
        s.login()
        return s

    def update(self):
        super().update()

        # Retrieve the Todoist project that contains the grocery list:
        todoist = self.get_todoist()
        proj = None
        rate_limit_retries_attempted = 0
        for attempt in range(self.todoist_rate_limit_retries):
            try:
                proj = self.get_project()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.log("Getting rate-limited by Todoist. Sleeping...")
                    time.sleep(self.todoist_rate_limit_timeout)
                    rate_limit_retries_attempted += 1
                else:
                    raise e

        # if we exhaused our retries, raise an exception
        if rate_limit_retries_attempted >= self.todoist_rate_limit_retries:
            raise Exception("Exceeded maximum retries due to Todoist rate limiting")

        # Retrieve all tasks stored in the grocery list. If there are no tasks,
        # we can return early since there's nothing to work with.
        tasks = todoist.get_tasks(project_id=proj.id)
        if len(tasks) == 0:
            return False

        # Connect to the chef service and retrieve a listing of all recipes.
        # We'll use this to determine if any of the tasks in the grocery list
        # match the name of a recipe, and thus should be expanded into a list of
        # ingredients.
        chef = self.get_chef_session()
        chef_result = chef.post("/recipes/list_all")
        if OracleSession.get_response_status(chef_result) != 200:
            self.log("Failed to retrieve recipe list from chef service: \"%s\"." % chef_result)
            return False
        recipes = OracleSession.get_response_json(chef_result)

        # Iterate through each task:
        tasks_resolved = 0
        for task in tasks:
            task_title = task.content.strip().lower()

            # Does this task already contain a magic string that indicates it
            # has already been processed? If so, skip it since we don't want to
            # process it again.
            keywords_to_skip = [
                EXPANDED_RECIPE_INGREDIENT_MAGIC,
                RECIPE_RESOLUTION_FAILURE_MAGIC,
            ]
            skip_task = False
            for keyword in keywords_to_skip:
                if keyword in task_title:
                    skip_task = True
                if task.description is not None and \
                   keyword in task.description.lower():
                    skip_task = True
            if skip_task:
                continue

            # Does the task's title contain the word "recipe"? If so, this means
            # the user is indicating that they want this task to be resolved as
            # a recipe.
            if RECIPE_MAGIC_STRING not in task_title:
                continue

            self.log("Found item that appears to be a recipe: \"%s\". Attempting to resolve..." % task.content)

            # Pass the task title (and description, if it exists) to the chef
            # service's resolve endpoint. If it matches up with a recognized
            # recipe, the chef service will return the recipe's ID (and other
            # information) to us.
            text_to_resolve = task_title
            if task.description is not None and len(task.description.strip()) > 0:
                text_to_resolve += "\n\n" + task.description.strip()
            chef_result = chef.post("/recipes/resolve", {
                "text": text_to_resolve,
            })
            chef_result_status = OracleSession.get_response_status(chef_result)
            if chef_result_status != 200:
                self.log("Failed to resolve recipe with chef service: \"%s\"." % chef_result)

                # If the status is 404, then there was no matching recipe to
                # match the text. To alert the user, we'll modify the todoist
                # item.
                if chef_result_status == 404:
                    new_title = "❓ %s" % task.content
                    new_desc = "(Could not find a matching recipe)\n\n"
                    if task.description is not None and len(task.description.strip()) > 0:
                        new_desc += task.description
                    new_desc += "\n\n%s" % RECIPE_RESOLUTION_FAILURE_MAGIC

                    todoist.update_task(
                        task.id,
                        title=new_title,
                        body=new_desc,
                    )

                # Skip to the next iteration
                continue
            recipe_info = OracleSession.get_response_json(chef_result)

            # Next, look up the full recipe information:
            chef_result = chef.post("/recipes/get_by_id", {
                "id": recipe_info["id"],
            })
            if OracleSession.get_response_status(chef_result) != 200:
                self.log("Failed to retrieve recipe from chef service: \"%s\"." % chef_result)
                continue
            recipe = Recipe.from_json(OracleSession.get_response_json(chef_result))
            self.log("Resolved item \"%s\" to recipe \"%s\"." % (
                task.content,
                recipe.id,
            ))

            # Finally, build a list of ingredients to represent the request;
            # we'll use these to add new tasks to the Todoist list.
            ingredients = []
            quantity_multiplier = float(recipe_info.get("quantity", 1.0))
            for ingredient in recipe.ingredients:
                i_json = ingredient.to_json()

                # Update the ingredient quantity by the quantity multiplier
                new_quantity = float(i_json.get("quantity", 1.0)) * float(quantity_multiplier)
                i_json["quantity"] = new_quantity

                # Parse updated JSON and append:
                i = Ingredient.from_json(i_json)
                ingredients.append(i)

            # For each ingredient, add a new task to the Todoist grocery list.
            for ingredient in ingredients:
                # Build a title string:
                title = ingredient.title if ingredient.title is not None else ingredient.id
                if quantity_multiplier != 1.0:
                    # If the quantity multiplier is an even integer, display it
                    # as an integer; otherwise, display it as a float with 2
                    # decimal places
                    if int(quantity_multiplier) == quantity_multiplier:
                        title += " (x%d)" % int(quantity_multiplier)
                    else:
                        title += " (%.2fx)" % quantity_multiplier

                # Next, build a description string.

                # Look at the ingredient's "is_optional" field, and if it's
                # marked as optional, add a note about that to the description.
                description = ""
                if ingredient.is_optional:
                    description += "(OPTIONAL) "

                # Also, look at the ingredient's replenish type, and add text
                # accordingly hinting that the user may already have this in
                # stock.
                if ingredient.replenish == IngredientReplenishType.SOMETIMES:
                    description += "(❗ You may already have this) "
                elif ingredient.replenish == IngredientReplenishType.RARELY:
                    description += "(‼️  You probably already have this) "

                # Next, add the recipe name and icon (if they exist):
                recipe_str = recipe.title if recipe.title is not None else recipe.id
                icon_str = ("%s " % recipe.icon) if recipe.icon is not None else ""
                description += "[%s%s]" % (icon_str, recipe_str)

                # Show the number of servings in the description, if the
                # quantity multiplier is not 1.0 (i.e. if the user has indicated
                # that they want to make more or less than the default number of
                # servings for the recipe).
                if quantity_multiplier != 1.0:
                    serving_count = recipe.servings * quantity_multiplier
                    if int(serving_count) == serving_count:
                        description += " - %d servings" % int(serving_count)
                    else:
                        description += " - %.2f servings" % serving_count

                # If the ingredient itself has a description, add that too.
                if ingredient.description is not None:
                    # If the ingredient has a description, add it to the task
                    # description.
                    description += "\n\n%s" % ingredient.description

                # Add the magic string to the description so that we don't end
                # up trying to resolve this ingredient as a recipe in the
                # future:
                description += "\n\n%s" % EXPANDED_RECIPE_INGREDIENT_MAGIC

                # Add the task to the list:
                todoist.add_task(
                    title,
                    description,
                    project_id=proj.id,
                )
                self.log("[Recipe: %s] Added ingredient \"%s\" to grocery list." % (
                    recipe.id,
                    title,
                ))

            # Remove the original task from the list, now that we've expanded it
            # into its ingredients.
            todoist.delete_task(task.id)
            self.log("[Recipe: %s] Removed original recipe item (\"%s\") from grocery list." % (
                recipe.id,
                task.content,
            ))
            tasks_resolved += 1

            # TODO - eventual improvement: add a `/ingredients/edit` endpoint,
            # which uses an LLM to examine a list of ingredients and compare it
            # against the original user string (TASK TITLE + TASK CONTENT) to
            # make edits to the ingredient list

        # Return according to whether or not we resolved any recipes:
        if tasks_resolved == 0:
            return False
        return True

