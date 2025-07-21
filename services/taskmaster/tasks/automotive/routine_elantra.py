# Imports
import os
import sys

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from tasks.automotive.routine import *

class TaskJob_Automotive_Routine_Maintenance_Elantra_5k(TaskJob_Automotive_Routine_Maintenance):
    def init(self):
        self.car_name = "Elantra"
        self.title = "5k-Mile Car Maintenance - %s" % self.car_name
        content_fname = "%s_5k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)
        self.trigger_months = [1, 2, 7, 8]
        self.trigger_days = range(1, 11)

class TaskJob_Automotive_Routine_Maintenance_Elantra_10k(TaskJob_Automotive_Routine_Maintenance):
    def init(self):
        self.car_name = "Elantra"
        self.title = "10k-Mile Car Maintenance - %s" % self.car_name
        content_fname = "%s_10k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)
        self.trigger_months = [1, 2]
        self.trigger_days = range(1, 11)

class TaskJob_Automotive_Routine_Maintenance_Elantra_20k(TaskJob_Automotive_Routine_Maintenance):
    def init(self):
        self.car_name = "Elantra"
        self.title = "20k-Mile Car Maintenance - %s" % self.car_name
        content_fname = "%s_20k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)
        self.trigger_years = list(range(2025, 2199, 2))
        self.trigger_months = [7, 8]
        self.trigger_days = range(1, 11)

