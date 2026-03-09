# Imports
import os
import sys
from datetime import datetime
import threading
import time
import inspect

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob, TaskConfig
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession

# String applied to items in the grocery list when they are created as a result
# of expanding a recipe prompt.
RECIPE_MAGIC_STRING = "recipe"
EXPANDED_RECIPE_INGREDIENT_MAGIC = "dimrod::expanded_recipe_ingredient"
RECIPE_RESOLUTION_FAILURE_MAGIC = "dimrod::recipe_resolution_failure"
AUTOSORT_IGNORE_MAGIC = "dimrod::autosort_ignore"

# Base class for the grocery list.
class TaskJob_Groceries(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Groceries")
        if proj is None:
            proj = todoist.add_project("Groceries", color="green")
        self.project = proj
        return proj

