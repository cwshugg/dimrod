# Imports
import os
import sys

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from tasks.automotive.carwash import *

class TaskJob_Automotive_Carwash_Focus(TaskJob_Automotive_Carwash):
    def init(self):
        self.car_name = "Focus"
        self.title = "Wash the Car - %s" % self.car_name

