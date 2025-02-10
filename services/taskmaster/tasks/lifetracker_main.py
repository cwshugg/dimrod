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

class TaskJob_LifeTracker_Main(TaskJob_LifeTracker):
    # Overridden initialization function.
    def init(self):
        super().init()
        self.config_name = "lifetracker_main.json"
    
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        success = False
        tracker = self.get_tracker()

        # iterate through all metrics within the tracker, and look for ones
        # that are ready to be triggered
        for metric in tracker.metrics:
            # retrieve the latest entry in the database for this metric
            latest_entry = tracker.get_latest_metric_entry(metric)
            latest_entry_timestamp = None
            if latest_entry is not None:
                latest_entry_timestamp = latest_entry.timestamp

            # if the metric is ready... send it as a menu
            if metric.trigger.is_ready(last_trigger=latest_entry_timestamp):
                self.log("Metric: \"%s\" is ready. "
                         "Sending menu via Telegram..." %
                         metric.name)
                menu = self.send_metric_menu(tracker, metric)

                # create a new entry in the metric database for this new menu
                entry = LifeMetricEntry()
                entry.init_defaults()
                entry.init_from_metric(metric)
                entry.init_from_telegram_menu(menu)
                tracker.save_metric_entry(entry)

                success = True

        # Next, we'll scan the metric database and do a few things:
        #   1. Look for any entries whose status is pending. Check the status
        #      of the menu with Telegram, and:
        #       1a. Update the entry's value if the user has responded.
        #       1b. Remove the entry if the Telegram menu has expired.
        entries = tracker.get_alive_metrics()
        for entry in entries:
            metric = tracker.get_metric_by_name(entry.metric_name)

            # get the corresponding Telegram menu. If the menu no longer
            # exists, then it must have expired.
            menu = self.get_metric_entry_menu(entry)
            if menu is None:
                # update the entry's status to DEAD and save the entry
                entry.status = LifeMetricEntryStatus.DEAD
                tracker.save_metric_entry(entry)

                # continue to the next iteration of the main loop
                continue
            
            # otherwise, iterate through the menu options and determine which
            # buttons were pressed. We'll use this information to update the
            # entry in the database.
            change_count = 0
            for (i, op) in enumerate(menu["options"]):
                # get the corresponding metric value field
                value = metric.values[i]
                
                # take the selection counts of each and update the entry's
                # selection counts
                field_name = value.get_sqlite3_column_name_selection_count()
                old_value = getattr(entry, field_name)
                new_value = op["selection_count"]
                setattr(entry, field_name, new_value)

                # if the new value was different, mark this down
                if old_value != new_value:
                    change_count += 1

            # if changes were made, write the entry back to the database
            if change_count > 0:
                tracker.save_metric_entry(entry)
                success = True

        return success

