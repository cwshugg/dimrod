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

# A taskjob that scans the grocery list for mention of recipe names.
# If a recipe name is found, the taskjob polls the chef service for the
# recipe's ingredients and adds them to the grocery list.
class TaskJob_Groceries_RecipeResolver(TaskJob_Groceries):
    def __init__(self):
        self.refresh_rate = 120

        self.chef_config_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "chef_config.json"
        )
        self.chef_config = None

    # Initializes and returns an authenticated session with the chef service.
    def get_chef_session(self):
        # Attempt to load the chef config file, if it hasn't been loaded yet.
        if self.chef_config is None:
            self.chef_config = OracleSessionConfig.from_file(self.chef_config_path)
        s = OracleSession(self.chef_config)
        s.login()
        return s

    def update(self, todoist, gcal):
        super().update(todoist, gcal)

        # Retrieve the Todoist project that contains the grocery list:
        proj = None
        rate_limit_retries_attempted = 0
        for attempt in range(self.todoist_rate_limit_retries):
            try:
                proj = self.get_project(todoist)
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

        # Retrieve all tasks stored in the grocery list.
        tasks = todoist.get_tasks(project_id=proj.id)

        print("TASKS:\n%s" % "\n".join([str(t) for t in tasks]))

        # Query the chef service for a list of all recipes.`
        chef = self.get_chef_session()
        # TODO


        return False

