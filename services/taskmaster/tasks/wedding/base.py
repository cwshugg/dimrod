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

# Base class for wedding-based tasks.
class TaskJob_Wedding(TaskJob):
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        return False

    def get_project(self, todoist):
        proj = todoist.get_project_by_name("Wedding")
        if proj is None:
            proj = todoist.add_project("Wedding", color="lavender")
        self.project = proj
        return proj

