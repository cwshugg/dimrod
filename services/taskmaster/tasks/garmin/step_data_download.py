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
from lib.garmin.database import GarminDatabaseStepsEntry
import lib.dtu as dtu
import lib.lu as lu

# Downloads and stores Garmin steps data locally.
class TaskJob_Garmin_StepDataDownload(TaskJob_Garmin):
    def init(self):
        super().init()
        self.api_call_delay = 5 # delay between Garmin API calls, to avoid rate limiting

    def update(self, todoist, gcal):
        # get an authenticated garmin client
        g = self.get_client()
        if g is None:
            return False

        # get a handle to the database
        db = self.get_database()

        # determine the last time we succeesfully downloaded step data, and use
        # this as our starting point (up until now) to retrieve step data
        now = datetime.now()
        steps_end = now
        last_success = self.get_last_success_datetime()
        steps_start = last_success
        if last_success is None:
            # if we've never successfully downloaded step data before, reach
            # back as far as possible, so we can download everything the Garmin
            # API has
            #
            # (this case should only happen on the very first run of this)
            steps_start = dtu.add_seconds(now, -1 * db.config.reachback_seconds)

        # split the range into chunks, so we call the API several times,
        # instead of one massive, instant call, to avoid rate limiting
        day_chunk_size = 7
        day_chunks = []
        day_chunk_dt = steps_start
        while day_chunk_dt < steps_end:
            chunk_start = day_chunk_dt
            chunk_end = dtu.add_days(chunk_start, day_chunk_size)

            # if we stepped past the end, crimp it off to be the end
            if chunk_end > steps_end:
                chunk_end = steps_end

            # append the range to the list, and step to the next chunk
            day_chunks.append([chunk_start, chunk_end])
            day_chunk_dt = chunk_end


        # determine what timezone we are in, so we can properly convert the
        # Garmin API's returned date strings into timezone-aware datetime
        # objects
        tz = lu.get_timezone()

        # iterate through each chunk and retrieve the steps data for that range
        # (and write the data out to disk)
        successful_data_writes = 0
        for chunk in day_chunks:
            chunk_start = chunk[0]
            chunk_end = chunk[1]

            # try to retrieve step data via the Garmin API for this chunk
            data = None
            try:
                data = g.get_steps_per_day(chunk_start, chunk_end)
                # iterate through each entry in the data and save it
                for day in data:
                    for entry in day:
                        obj = GarminDatabaseStepsEntry.from_garmin_json(entry, timezone=tz)

                        # write the entry to the database
                        db.save_steps(obj)
                        self.log("Saved Garmin steps data: %s - %s: %d steps" %
                                 (obj.time_start.isoformat(),
                                  obj.time_end.isoformat(),
                                  obj.step_count))

                        successful_data_writes += 1
            except Exception as e:
                self.log("Failed to retrieve Garmin steps data for range %s - %s: %s" %
                         (dtu.format_yyyymmdd(chunk[0]),
                          dtu.format_yyyymmdd(chunk[1]),
                          e))

            # wait a bit between API calls, to avoid rate limiting
            time.sleep(self.api_call_delay)

        # if we successfully wrote any data, return to indicate success.
        # Otherwise, return failure
        if successful_data_writes > 0:
            return True
        retrun False

