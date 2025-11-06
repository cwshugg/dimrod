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
                                GarminDatabaseVO2MaxEntry
import lib.dtu as dtu
import lib.lu as lu

# Downloads and stores Garmin data locally.
class TaskJob_Garmin_DataDownload(TaskJob_Garmin):
    def init(self):
        super().init()
        self.api_call_delay = 5 # delay between Garmin API calls, to avoid rate limiting

        # define various "reachbacks" for when we retrieve all past data for
        # the first time
        self.reachback_steps = 86400 * (365 / 2)    # 6 months
        self.reachback_sleep = 86400 * (365 * 5)    # 5 years
        self.reachback_vo2max = 86400 * (365 * 5)    # 5 years

    def update(self, todoist, gcal):
        # get an authenticated garmin client
        g = self.get_client()
        if g is None:
            return False

        # get a handle to the database
        db = self.get_database()

        # determine what timezone we are in, so we can properly convert the
        # Garmin API's returned date strings into timezone-aware datetime
        # objects
        tz = lu.get_timezone()

        successful_data_writes = 0
        successful_data_writes += self.download_sleep(g, db, tz)
        successful_data_writes += self.download_vo2max(g, db, tz)
        successful_data_writes += self.download_steps(g, db, tz)

        # TODO - activities
        # TODO - heart rate

        # if we successfully wrote any data, return to indicate success.
        # Otherwise, return failure
        if successful_data_writes > 0:
            return True
        return False

    # Takes in a timerange and turns it into a list of day chunks, each
    # containing a timerange that is a subset of the overall range.
    #
    # This is used to query the Garmin API a little at a time, to avoid rate
    # limiting.
    def get_day_chunks(self, timerange_start, timerange_end, day_chunk_size: int = 7):
        # split the range into chunks, so we call the API several times,
        # instead of one massive, instant call, to avoid rate limiting
        day_chunks = []
        day_chunk_dt = timerange_start
        while day_chunk_dt < timerange_end:
            chunk_start = day_chunk_dt
            chunk_end = dtu.add_days(chunk_start, day_chunk_size)

            # if we stepped past the end, crimp it off to be the end
            if chunk_end > timerange_end:
                chunk_end = timerange_end

            # append the range to the list, and step to the next chunk
            day_chunks.append([chunk_start, chunk_end])
            day_chunk_dt = chunk_end
        return day_chunks

    # ------------------------------ Step Data ------------------------------- #
    # Determines the timerange to download step data for.
    def get_timerange_steps(self, db: GarminDatabase):
        now = datetime.now()
        timerange_end = now

        # look for the latest entry in the database. We'll use this as a basis
        # for how far back to start downloading data
        last_entry = db.search_steps_latest()
        timerange_start = None
        if last_entry is None:
            # if we've never successfully downloaded data before, reach back
            # very far, so we can download everything the Garmin API has
            #
            # (this case should only happen on the very first run of this)
            timerange_start = dtu.add_seconds(now, -1 * self.reachback_steps)
        else:
            # if we have a last entry, move back a few days to ensure we
            # capture any recent updates to existing entries
            timerange_start = dtu.add_days(last_entry.time_start, -2)

        return [timerange_start, timerange_end]

    # Downloads step data.
    # Returns the number of writes made to the database.
    def download_steps(self,
                       g: Garmin,
                       db: GarminDatabase,
                       tz):
        (timerange_start, timerange_end) = self.get_timerange_steps(db)
        day_chunks = self.get_day_chunks(timerange_start, timerange_end)

        # iterate through each chunk and retrieve the steps data for that range
        # (and write the data out to disk)
        successful_data_writes = 0
        for chunk in day_chunks:
            chunk_start = chunk[0]
            chunk_end = chunk[1]

            # try to retrieve step data via the Garmin API for this chunk
            try:
                data = g.get_steps_for_day_range(chunk_start, chunk_end)
                # iterate through each entry in the data and save it
                for day in data:
                    for entry in day:
                        # try to parse an object from the Garmin data; skip it
                        # on failure
                        try:
                            obj = GarminDatabaseStepsEntry.from_garmin_json(entry, tz=tz)
                        except Exception as e:
                            #self.log("Failed to parse Garmin steps data entry: %s. Skipping" % e)
                            continue

                        # write the entry to the database
                        db.save_steps(obj)
                        self.log("Saved Garmin steps data: %s - %s: %d steps (%s)" %
                                 (dtu.format_yyyymmdd_hhmmss_12h(obj.time_start),
                                  dtu.format_yyyymmdd_hhmmss_12h(obj.time_end),
                                  obj.step_count,
                                  obj.activity_level))

                        successful_data_writes += 1
            except Exception as e:
                self.log("Failed to retrieve Garmin steps data for range %s - %s: %s" %
                         (dtu.format_yyyymmdd(chunk[0]),
                          dtu.format_yyyymmdd(chunk[1]),
                          e))

            # wait a bit between API calls, to avoid rate limiting
            time.sleep(self.api_call_delay)

        return successful_data_writes

    # ------------------------------ Sleep Data ------------------------------ #
    # Determines the timerange to download sleep data for.
    def get_timerange_sleep(self, db: GarminDatabase):
        now = datetime.now()
        timerange_end = now

        # look for the latest entry in the database. We'll use this as a basis
        # for how far back to start downloading data
        last_entry = db.search_sleep_latest()
        timerange_start = None
        if last_entry is None:
            # if we've never successfully downloaded data before, reach back
            # very far, so we can download everything the Garmin API has
            #
            # (this case should only happen on the very first run of this)
            timerange_start = dtu.add_seconds(now, -1 * self.reachback_sleep)
        else:
            # if we have a last entry, move back a few days to ensure we
            # capture any recent updates to existing entries
            timerange_start = dtu.add_days(last_entry.time_start, -2)

        return [timerange_start, timerange_end]

    # Downloads sleep data.
    # Returns the number of writes made to the database.
    def download_sleep(self,
                       g: Garmin,
                       db: GarminDatabase,
                       tz):
        (timerange_start, timerange_end) = self.get_timerange_sleep(db)
        day_chunks = self.get_day_chunks(timerange_start, timerange_end)

        successful_data_writes = 0
        for chunk in day_chunks:
            chunk_start = chunk[0]
            chunk_end = chunk[1]

            try:
                data = g.get_sleep_for_day_range(chunk_start, chunk_end)
                # iterate through each entry in the data and save it
                for sleep_data in data:
                    # attempt to parse the sleep entry object. If it fails, skip
                    obj = None
                    try:
                        obj = GarminDatabaseSleepEntry.from_garmin_json(sleep_data, tz=tz)
                    except Exception as e:
                        #self.log("Failed to parse Garmin sleep data entry: %s. Skipping" % e)
                        continue

                    # write the entry to the database
                    db.save_sleep(obj)
                    self.log("Saved Garmin sleep data: %s - %s: %.2f hours of sleep" %
                             (dtu.format_yyyymmdd_hhmmss_12h(obj.time_start),
                              dtu.format_yyyymmdd_hhmmss_12h(obj.time_end),
                              float(float(obj.sleep_time_total_seconds) / 3600.0)))

                    successful_data_writes += 1
            except Exception as e:
                self.log("Failed to retrieve Garmin sleep data for range %s - %s: %s" %
                         (dtu.format_yyyymmdd(chunk[0]),
                          dtu.format_yyyymmdd(chunk[1]),
                          e))

            # wait a bit between API calls, to avoid rate limiting
            time.sleep(self.api_call_delay)

        return successful_data_writes

    # ----------------------------- VO2Max Data ------------------------------ #
    # Determines the timerange to download vo2max data for.
    def get_timerange_vo2max(self, db: GarminDatabase):
        now = datetime.now()
        timerange_end = now

        # look for the latest entry in the database. We'll use this as a basis
        # for how far back to start downloading data
        last_entry = db.search_vo2max_latest()
        timerange_start = None
        if last_entry is None:
            # if we've never successfully downloaded data before, reach back
            # very far, so we can download everything the Garmin API has
            #
            # (this case should only happen on the very first run of this)
            timerange_start = dtu.add_seconds(now, -1 * self.reachback_vo2max)
        else:
            # if we have a last entry, move back a few days to ensure we
            # capture any recent updates to existing entries
            timerange_start = dtu.add_days(last_entry.timestamp, -2)

        return [timerange_start, timerange_end]

    # Downloads vo2max data.
    # Returns the number of writes made to the database.
    def download_vo2max(self,
                       g: Garmin,
                       db: GarminDatabase,
                       tz):
        (timerange_start, timerange_end) = self.get_timerange_vo2max(db)
        day_chunks = self.get_day_chunks(timerange_start, timerange_end)

        successful_data_writes = 0
        for chunk in day_chunks:
            chunk_start = chunk[0]
            chunk_end = chunk[1]

            try:
                data = g.get_vo2max_for_day_range(chunk_start, chunk_end)
                # iterate through each entry in the data and save it
                for vo2max_data in data:
                    # attempt to parse the vo2max entry object. If it fails, skip
                    obj = None
                    try:
                        obj = GarminDatabaseVO2MaxEntry.from_garmin_json(vo2max_data, tz=tz)
                    except Exception as e:
                        #self.log("Failed to parse Garmin vo2max data entry: %s. Skipping" % e)
                        continue

                    # write the entry to the database
                    db.save_vo2max(obj)
                    self.log("Saved Garmin vo2max data: %s: VO2Max: %.2f, Fitness Age: %d" %
                             (dtu.format_yyyymmdd_hhmmss_12h(obj.timestamp),
                              obj.vo2max,
                              obj.fitness_age))

                    successful_data_writes += 1
            except Exception as e:
                self.log("Failed to retrieve Garmin vo2max data for range %s - %s: %s" %
                         (dtu.format_yyyymmdd(chunk[0]),
                          dtu.format_yyyymmdd(chunk[1]),
                          e))

            # wait a bit between API calls, to avoid rate limiting
            time.sleep(self.api_call_delay)

        return successful_data_writes

