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

# Base class for chores.
class TaskJob_Chores(TaskJob):
    def update(self, todoist):
        super().update(todoist)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Chores")
        if proj is None:
            proj = todoist.add_project("Chores", color="green")
        self.project = proj
        return proj

# Base class for automotive-based chores.
class TaskJob_Chores_Automotive(TaskJob_Chores):
    def get_section(self, todoist):
        proj = self.get_project(todoist)
        sect = todoist.get_section_by_name("Automotive")
        if sect is None:
            sect = todoist.add_section("Automotive", proj.id)
        self.section = sect
        return sect

