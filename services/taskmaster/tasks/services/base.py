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

class TaskJob_Services(TaskJob):
    """Base class for service/software/API-related tasks."""
    def update(self):
        super().update()
        return False

    def get_project(self):
        todoist = self.get_todoist()
        proj = todoist.get_project_by_name("Household")
        if proj is None:
            proj = todoist.add_project("Household", color="yellow")
        self.project = proj
        return proj

