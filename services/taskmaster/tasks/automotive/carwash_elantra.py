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

class TaskJob_Automotive_Carwash_Elantra(TaskJob_Automotive_Carwash):
    def init(self):
        super().init()
        self.car_name = "Elantra"
        self.title = "Wash the Car - %s" % self.car_name
        content_fname = __file__.replace(".py", ".md")
        self.content = os.path.join(fdir, content_fname)

