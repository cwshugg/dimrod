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

# Base class for automotive-based chores.
class TaskJob_Automotive(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        return self.get_project_by_name("Automotive", color="charcoal")

# Base class for medical-based chores.
class TaskJob_Medical(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Chores")
        if proj is None:
            proj = todoist.add_project("Medical", color="blue")
        self.project = proj
        return proj

