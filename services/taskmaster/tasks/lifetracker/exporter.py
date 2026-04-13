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
from tasks.lifetracker.base import *
import lib.dtu as dtu
from lib.config import Config, ConfigField

class TaskJob_LifeTracker_Exporter(TaskJob_LifeTracker):
    def init(self):
        """Overridden initialization function."""
        super().init()
        self.refresh_rate = 604800 # (one week)
        self.config_name = "main.json"

        self.spreadsheet_name = "lifetracker.xlsx"
        self.spreadsheet_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            self.spreadsheet_name,
        )

    def update(self):
        super().update()
        # Get a database interface wrapper for the tracker's database, and
        # export it to a spreadsheet at the configured path.
        tracker = self.get_tracker()
        db = tracker.get_database()
        if os.path.exists(self.spreadsheet_path):
            os.remove(self.spreadsheet_path)
        self.log("Exporting lifetracker metrics to spreadsheet at: \"%s\"" % self.spreadsheet_path)
        db.export_to_excel(self.spreadsheet_path)
        self.log("Export complete.")

        return True

