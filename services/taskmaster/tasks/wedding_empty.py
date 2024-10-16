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
from task import TaskConfig
from tasks.base import *
import lib.dtu as dtu

class TaskJob_Wedding_Empty(TaskJob_Wedding):
    def update(self, todoist, gcal):
        proj = self.get_project(todoist)

        # this is empty; it simply ensures that the project exists
        return False

