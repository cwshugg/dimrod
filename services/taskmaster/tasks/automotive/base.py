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

class TaskJob_Automotive(TaskJob):
    """Base class for automotive-based tasks."""
    def update(self):
        super().update()
        return False

    def get_project(self):
        return self.get_project_by_name("Automotive", color="red")

