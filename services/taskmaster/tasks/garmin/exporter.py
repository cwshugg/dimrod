# Imports
import os
import sys
from datetime import datetime
import time

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig
from tasks.garmin.base import *
from lib.garmin.database import GarminDatabaseStepsEntry, \
                                GarminDatabaseSleepEntry, \
                                GarminDatabaseVO2MaxEntry, \
                                GarminDatabaseHeartRateSummaryEntry, \
                                GarminDatabaseHeartRateEntry, \
                                GarminDatabaseActivityEntry
import lib.dtu as dtu
import lib.lu as lu
from lib.db import Database, DatabaseConfig

class TaskJob_Garmin_Exporter(TaskJob_Garmin):
    """Exports data from the database into a spreadsheet."""
    def init(self):
        super().init()
        self.refresh_rate = 604800 # (one week)

        self.spreadsheet_name = "garmin_data.xlsx"
        self.spreadsheet_path = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            self.spreadsheet_name,
        )


    def update(self):
        super().update()

        # Get a database interface wrapper for the garmin database
        garmin_db = self.get_database()
        db = Database(DatabaseConfig.from_json({
            "path": garmin_db.config.db_path,
        }))

        # Delete the old spreadsheet and export it:
        if os.path.exists(self.spreadsheet_path):
            os.remove(self.spreadsheet_path)
        self.log("Exporting garmin data to spreadsheet at: \"%s\"" % self.spreadsheet_path)
        db.export_to_excel(self.spreadsheet_path)
        self.log("Export complete.")

        return True

