# Base class for chore-based tasks.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob, TaskConfig
import lib.dtu as dtu

# Base class for automotive-based tasks.
class TaskJob_Automotive(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        return self.get_project_by_name(todoist, "Automotive", color="red")

# Base class for medical-based tasks.
class TaskJob_Medical(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Medical")
        if proj is None:
            proj = todoist.add_project("Medical", color="blue")
        self.project = proj
        return proj

# Base class for finance-based tasks.
class TaskJob_Finance(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Finances")
        if proj is None:
            proj = todoist.add_project("Finances", color="olive_green")
        self.project = proj
        return proj

# Base class for house chores and maintenance.
class TaskJob_Household(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Household")
        if proj is None:
            proj = todoist.add_project("Household", color="yellow")
        self.project = proj
        return proj

# Base class for the grocery list.
class TaskJob_Groceries(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Groceries")
        if proj is None:
            proj = todoist.add_project("Groceries", color="green")
        self.project = proj
        return proj

