# This module implements code to work with Garmin connect data.

# Imports
import os
import sys
import enum
from datetime import datetime, timezone
import sqlite3
import hashlib

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu


# ====================== Generic Database Entry Objects ====================== #
class GarminDatabaseEntryBase(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=False, default=None),
        ]

    # Turns the entry's start and end time into a unique ID string, such that
    # the exact same start/end times will produce the same ID string.
    def get_id(self):
        if self.id is None:
            # if this object has a start and end time, we'll use both of them
            # to generate a unique timestamp, along with the class name
            hash_str = self.__class__.__name__ + "|"
            if self.get_field("time_start") is not None and \
               self.get_field("time_end") is not None:
                # convert the timestamps to UTC before generating the ID
                tstart_utc = self.time_start.astimezone(tz=timezone.utc)
                tend_utc = self.time_end.astimezone(tz=timezone.utc)

                hash_str += "%s-%s" % (tstart_utc.isoformat(),
                                       tend_utc.isoformat())
            elif self.get_field("timestamp") is not None:
                # convert the timestamp to UTC before generating the ID
                t_utc = self.timestamp.astimezone(tz=timezone.utc)
                hash_str += "%s" % (t_utc.isoformat())
            else:
                assert False, "Cannot generate ID for GarminDatabaseEntryBase without `time_start`/`time_end` or `timestamp` fields."

            # encode the string to utf-8 and hash it
            data = hash_str.encode("utf-8")
            self.id = hashlib.sha256(data).hexdigest()
        return self.id


# ================================ Step Data ================================= #
# Represents a single database entry for Garmin step data.
class GarminDatabaseStepsEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("step_count",       [int],      required=True),
            ConfigField("push_count",       [int],      required=False, default=0),
            ConfigField("activity_level",   [str],      required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        # parse the times and attach a UTC timezone (since these are in GMT).
        # `strptime` won't be aware of the timezone due to the format the
        # string comes in, so we have to set UTC manually
        time_start = datetime.strptime(jdata["startGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        time_end = datetime.strptime(jdata["endGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        time_start = time_start.replace(tzinfo=timezone.utc)
        time_end = time_end.replace(tzinfo=timezone.utc)

        # if a timezone was given, convert the UTC-based timestamp to this
        # timezone
        if tz is not None:
            time_start = time_start.astimezone(tz=tz)
            time_end = time_end.astimezone(tz=tz)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "step_count": jdata["steps"],
            "push_count": jdata.get("pushes", 0),
            "activity_level": jdata.get("primaryActivityLevel", None)
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "step_count"]


# ================================ Sleep Data ================================ #
class GarminDatabaseSleepMovementEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("movement_level",   [float], required=True),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        # parse the times and attach a UTC timezone (since these are in GMT).
        # `strptime` won't be aware of the timezone due to the format the
        # string comes in, so we have to set UTC manually
        time_start = datetime.strptime(jdata["startGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        time_end = datetime.strptime(jdata["endGMT"], "%Y-%m-%dT%H:%M:%S.%f")
        time_start = time_start.replace(tzinfo=timezone.utc)
        time_end = time_end.replace(tzinfo=timezone.utc)

        # if a timezone was given, convert the UTC-based timestamp to this
        # timezone
        if tz is not None:
            time_start = time_start.astimezone(tz=tz)
            time_end = time_end.astimezone(tz=tz)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "movement_level": jdata["activityLevel"],
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "movement_level"]

class GarminDatabaseSleepHeartRateEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp", [datetime], required=True),
            ConfigField("heartrate", [int], required=True),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        timestamp = datetime.fromtimestamp(jdata["startGMT"] / 1000.0, tz=timezone.utc)
        if tz is not None:
            timestamp = timestamp.astimezone(tz=tz)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "heartrate": jdata["value"],
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "heartrate"]

# Represents a single database entry for Garmin sleep data.
class GarminDatabaseSleepEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("sleep_time_total_seconds", [int],      required=True),
            ConfigField("sleep_time_deep_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_light_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_rem_sleep_seconds", [int], required=True),
            ConfigField("sleep_time_awake_seconds", [int], required=True),
            ConfigField("respiration_min", [float], required=True),
            ConfigField("respiration_max", [float], required=True),
            ConfigField("respiration_avg", [float], required=True),
            ConfigField("heartrate_resting", [int], required=True),
            ConfigField("movement", [GarminDatabaseSleepMovementEntry], required=False, default=None),
            ConfigField("heartrate", [GarminDatabaseSleepHeartRateEntry], required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        dto = jdata["dailySleepDTO"]

        # if the start or ending time is not defined, refuse to parse
        if dto["sleepStartTimestampGMT"] is None or \
           dto["sleepEndTimestampGMT"] is None:
            raise ValueError("Cannot parse Garmin sleep data without valid start/end times")

        time_start = datetime.fromtimestamp(dto["sleepStartTimestampGMT"] / 1000.0, tz=timezone.utc)
        time_end = datetime.fromtimestamp(dto["sleepEndTimestampGMT"] / 1000.0, tz=timezone.utc)
        if tz is not None:
            time_start = time_start.astimezone(tz=tz)
            time_end = time_end.astimezone(tz=tz)

        # create an object by providing it with a JSON structure it can parse
        json_data = {
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "sleep_time_total_seconds": dto["sleepTimeSeconds"],
            "sleep_time_deep_sleep_seconds": dto["deepSleepSeconds"],
            "sleep_time_light_sleep_seconds": dto["lightSleepSeconds"],
            "sleep_time_rem_sleep_seconds": dto["remSleepSeconds"],
            "sleep_time_awake_seconds": dto["awakeSleepSeconds"],
            "respiration_min": dto["lowestRespirationValue"],
            "respiration_max": dto["highestRespirationValue"],
            "respiration_avg": dto["averageRespirationValue"],
            "heartrate_resting": jdata["restingHeartRate"],
        }

        # if movement data is available, include it
        if "sleepMovement" in jdata and \
           jdata["sleepMovement"] is not None and \
           len(jdata["sleepMovement"]) > 0:
            mdata = jdata["sleepMovement"]
            movement_entries = []
            for mentry in mdata:
                movement_entry = GarminDatabaseSleepMovementEntry.from_garmin_json(
                    mentry,
                    tz=tz
                )
                movement_entries.append(movement_entry.to_json())
            json_data["movement"] = movement_entries

        # if heartrate data is available, include it
        if "sleepHeartRate" in jdata and \
           jdata["sleepHeartRate"] is not None and \
           len(jdata["sleepHeartRate"]) > 0:
            hrdata = jdata["sleepHeartRate"]
            hr_entries = []
            for hre in hrdata:
                # if the heartrate value is missing, skip it
                if "value" not in hre or hre["value"] is None:
                    continue

                # parse the heartrate data into an entry object
                hr_entry = GarminDatabaseSleepHeartRateEntry.from_garmin_json(
                    hre,
                    tz=tz
                )
                hr_entries.append(hr_entry.to_json())
            json_data["heartrate"] = hr_entries

        # parse the final object from JSON, and generate its ID, before
        # returning it
        entry = cls.from_json(json_data)
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return [
            "id",
            "time_start",
            "time_end",
            "sleep_time_total_seconds",
            "sleep_time_deep_sleep_seconds",
            "sleep_time_light_sleep_seconds",
            "sleep_time_rem_sleep_seconds",
            "sleep_time_awake_seconds",
            "respiration_min",
            "respiration_max",
            "respiration_avg",
            "heartrate_resting",
        ]


# =============================== VO2Max Data ================================ #
# Represents a single database entry for Garmin vo2max data.
class GarminDatabaseVO2MaxEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp",       [datetime],  required=True),
            ConfigField("vo2max",          [float],     required=True),
            ConfigField("fitness_age",     [int],       required=True),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        # if the provided JSON object is a list, grab the first value
        if type(jdata) == list:
            assert len(jdata) > 0, "Garmin VO2Max JSON data is empty"
            jdata = jdata[0]

        # the "generic" field should be present; this contains the VO2Max data
        # points
        assert "generic" in jdata, "Garmin VO2Max JSON data missing \"generic\" field"
        gdata = jdata["generic"]

        # parse the calendar date as a datetime; by default, set to UTC
        timestamp = datetime.strptime(gdata["calendarDate"], "%Y-%m-%d")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        # if a timezone was given, assign it to the calendar date
        if tz is not None:
            timestamp = timestamp.replace(tzinfo=tz)

        # get the vo2max value (prefer the precise value, but if it's not
        # present, get the non-precise value
        vo2max = gdata.get("vo2MaxPreciseValue", None)
        if vo2max is None:
            vo2max = gdata.get("vo2MaxValue", None)

        # get the fitness age
        fitness_age = gdata["fitnessAge"]

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "vo2max": vo2max,
            "fitness_age": fitness_age,
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "timestamp", "vo2max", "fitness_age"]


# ============================= Heart Rate Data ============================== #
# Represents a single timestamped heart rate data point.
class GarminDatabaseHeartRateEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp", [datetime], required=True),
            ConfigField("heartrate", [int], required=True),
        ]

    @classmethod
    def from_garmin_list(cls, ldata: list, tz=None):
        # the data is provided like so:
        #
        #    [1762059600000, 73]
        assert len(ldata) == 2, "Invalid Garmin heart rate list data length; " \
                                "expected two entries: a timestamp and a heart rate value"

        # parse the timestamp and apply the timezone
        timestamp = datetime.fromtimestamp(ldata[0] / 1000.0, tz=timezone.utc)
        if tz is not None:
            timestamp = timestamp.astimezone(tz=tz)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "heartrate": ldata[1],
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "timestamp", "heartrate"]

# Represents a single database entry for Garmin daily heart rate summary.
class GarminDatabaseHeartRateSummaryEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("timestamp",            [datetime], required=True),
            ConfigField("heartrate_min",        [int],      required=False, default=None),
            ConfigField("heartrate_max",        [int],      required=False, default=None),
            ConfigField("heartrate_resting",    [int],      required=False, default=None),
            ConfigField("heartrate_resting_avg_last_7days", [int], required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        # parse the calendar date as a datetime; by default, set to UTC
        timestamp = datetime.strptime(jdata["calendarDate"], "%Y-%m-%d")
        timestamp = timestamp.replace(tzinfo=timezone.utc)

        # if a timezone was given, assign it to the calendar date
        if tz is not None:
            timestamp = timestamp.replace(tzinfo=tz)

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "timestamp": timestamp.isoformat(),
            "heartrate_min": jdata.get("minHeartRate", None),
            "heartrate_max": jdata.get("maxHeartRate", None),
            "heartrate_resting": jdata.get("restingHeartRate", None),
            "heartrate_resting_avg_last_7days": jdata.get("lastSevenDaysAvgRestingHeartRate", None),
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return [
            "id",
            "timestamp",
            "heartrate_min",
            "heartrate_max",
            "heartrate_resting",
        ]


# ============================== Activity Data =============================== #
# Represents a single exercise set entry for a strength training activity.
class GarminDatabaseExerciseSetEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("category",         [str],      required=False, default=None),
            ConfigField("reps",             [int],      required=False, default=None),
            ConfigField("sets",             [int],      required=False, default=None),
            ConfigField("weight_max",       [int],      required=False, default=None),
            ConfigField("volume",           [int],      required=False, default=None),
            ConfigField("duration",         [float],    required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        entry = cls.from_json({
            "category": jdata.get("category", None),
            "reps": jdata.get("reps", None),
            "sets": jdata.get("sets", None),
            "weight_max": jdata.get("maxWeight", None),
            "volume": jdata.get("volume", None),
            "duration": jdata.get("duration", None),
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    # Overridden to generate an ID based on other fields.
    # This ID isn't necessarily unique, but it's good enough for my purposes.
    def get_id(self):
        hash_str = self.__class__.__name__ + "|" + \
                   str(self.category) + "|" + \
                   str(self.reps) + "|" + \
                   str(self.sets) + "|" + \
                   str(self.weight_max) + "|" + \
                   str(self.volume) + "|" + \
                   str(self.duration)
        data = hash_str.encode("utf-8")
        self.id = hashlib.sha256(data).hexdigest()
        return self.id


# Represents a single database entry for Garmin activity data.
class GarminDatabaseActivityEntry(GarminDatabaseEntryBase):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("time_start",       [datetime], required=True),
            ConfigField("time_end",         [datetime], required=True),
            ConfigField("activity_id",      [str],      required=True),
            ConfigField("name",             [str],      required=False, default=None),
            ConfigField("activity_type",    [str],      required=False, default=None),
            ConfigField("event_type",       [str],      required=False, default=None),
            ConfigField("distance",         [float],    required=False, default=None),
            ConfigField("duration_total",   [float],    required=False, default=None),
            ConfigField("duration_moving",  [float],    required=False, default=None),
            ConfigField("elevation_gain",   [float],    required=False, default=None),
            ConfigField("elevation_loss",   [float],    required=False, default=None),
            ConfigField("elevation_min",    [float],    required=False, default=None),
            ConfigField("elevation_max",    [float],    required=False, default=None),
            ConfigField("speed_avg",        [float],    required=False, default=None),
            ConfigField("speed_max",        [float],    required=False, default=None),
            ConfigField("speed_vertical_max", [float],  required=False, default=None),
            ConfigField("latitude_start",   [float],    required=False, default=None),
            ConfigField("longitude_start",  [float],    required=False, default=None),
            ConfigField("latitude_end",     [float],    required=False, default=None),
            ConfigField("longitude_end",    [float],    required=False, default=None),
            ConfigField("calories",         [float],    required=False, default=None),
            ConfigField("heartrate_avg",    [float],    required=False, default=None),
            ConfigField("heartrate_max",    [float],    required=False, default=None),
            ConfigField("heartrate_time_in_zone1", [float], required=False, default=None),
            ConfigField("heartrate_time_in_zone2", [float], required=False, default=None),
            ConfigField("heartrate_time_in_zone3", [float], required=False, default=None),
            ConfigField("heartrate_time_in_zone4", [float], required=False, default=None),
            ConfigField("heartrate_time_in_zone5", [float], required=False, default=None),
            ConfigField("cycling_cadence_rpm_avg", [float], required=False, default=None),
            ConfigField("cycling_cadence_rpm_max", [float], required=False, default=None),
            ConfigField("laps",             [int],      required=False, default=None),
            ConfigField("power_avg",        [float],    required=False, default=None),
            ConfigField("power_max",        [float],    required=False, default=None),
            ConfigField("power_norm",       [float],    required=False, default=None),
            ConfigField("power_max_avg_1sec", [int],    required=False, default=None),
            ConfigField("power_max_avg_2sec", [int],    required=False, default=None),
            ConfigField("power_max_avg_5sec", [int],    required=False, default=None),
            ConfigField("power_max_avg_10sec", [int],   required=False, default=None),
            ConfigField("power_max_avg_20sec", [int],   required=False, default=None),
            ConfigField("power_max_avg_30sec", [int],   required=False, default=None),
            ConfigField("power_max_avg_60sec", [int],   required=False, default=None),
            ConfigField("power_max_avg_120sec", [int],  required=False, default=None),
            ConfigField("power_max_avg_300sec", [int],  required=False, default=None),
            ConfigField("power_max_avg_600sec", [int],  required=False, default=None),
            ConfigField("power_max_avg_1200sec", [int], required=False, default=None),
            ConfigField("power_max_avg_1800sec", [int], required=False, default=None),
            ConfigField("power_time_in_zone1", [float], required=False, default=None),
            ConfigField("power_time_in_zone2", [float], required=False, default=None),
            ConfigField("power_time_in_zone3", [float], required=False, default=None),
            ConfigField("power_time_in_zone4", [float], required=False, default=None),
            ConfigField("power_time_in_zone5", [float], required=False, default=None),
            ConfigField("power_time_in_zone6", [float], required=False, default=None),
            ConfigField("power_time_in_zone7", [float], required=False, default=None),
            ConfigField("sets_total",       [int],      required=False, default=None),
            ConfigField("sets_active",      [int],      required=False, default=None),
            ConfigField("reps_total",       [int],      required=False, default=None),
            ConfigField("exercise_sets",    [GarminDatabaseExerciseSetEntry], required=False, default=None),
        ]

    @classmethod
    def from_garmin_json(cls, jdata: dict, tz=None):
        # if the provided JSON object is a list, grab the first value
        if type(jdata) == list:
            assert len(jdata) > 0, "Garmin activity JSON data is empty"
            jdata = jdata[0]

        time_start = datetime.strptime(jdata["startTimeGMT"], "%Y-%m-%d %H:%M:%S")
        time_start = time_start.replace(tzinfo=timezone.utc)
        time_end = None

        # some older activities don't come with an endtime. If that's the case,
        # we'll come up with one
        if "endTimeGMT" not in jdata or jdata["endTimeGMT"] is None:
            # look for a duration value to add to the start time
            duration = jdata.get("elapsedDuration", None)
            if duration is None:
                duration = jdata.get("duration", None)
            assert duration is not None, "Garmin activity JSON data missing end time or duration"

            # add the duration to the start time
            time_end = dtu.add_seconds(time_start, int(duration))
        else:
            # or, just parse the provided ending time
            time_end = datetime.strptime(jdata["endTimeGMT"], "%Y-%m-%d %H:%M:%S")
            time_end = time_end.replace(tzinfo=timezone.utc)

        # if a timezone was given, convert the UTC-based timestamp to this
        # timezone
        if tz is not None:
            time_start = time_start.astimezone(tz=tz)
            time_end = time_end.astimezone(tz=tz)

        # try to determine the activity type
        activity_type = None
        if "activityType" in jdata:
            if "typeKey" in jdata["activityType"]:
                activity_type = jdata["activityType"]["typeKey"]

        # try to determine the event type
        event_type = None
        if "eventType" in jdata:
            if "typeKey" in jdata["eventType"]:
                event_type = jdata["eventType"]["typeKey"]

        # were exercise sets provided? If so, parse them
        exercise_sets = None
        if "summarizedExerciseSets" in jdata and \
           jdata["summarizedExerciseSets"] is not None and \
           len(jdata["summarizedExerciseSets"]) > 0:
            exercise_sets = []
            for eset in jdata["summarizedExerciseSets"]:
                eset_entry = GarminDatabaseExerciseSetEntry.from_garmin_json(
                    eset,
                    tz=tz
                )
                exercise_sets.append(eset_entry.to_json())

        # create an object by providing it with a JSON structure it can parse
        entry = cls.from_json({
            "time_start": time_start.isoformat(),
            "time_end": time_end.isoformat(),
            "activity_id": str(jdata["activityId"]),
            "name": jdata.get("activityName", None),
            "activity_type": activity_type,
            "event_type": event_type,
            "distance": jdata.get("distance", None),
            "duration_total": jdata.get("elapsedDuration", None),
            "duration_moving": jdata.get("movingDuration", None),
            "elevation_gain": jdata.get("elevationGain", None),
            "elevation_loss": jdata.get("elevationLoss", None),
            "elevation_min": jdata.get("minElevation", None),
            "elevation_max": jdata.get("maxElevation", None),
            "speed_avg": jdata.get("averageSpeed", None),
            "speed_max": jdata.get("maxSpeed", None),
            "speed_vertical_max": jdata.get("maxVerticalSpeed", None),
            "latitude_start": jdata.get("startLatitude", None),
            "longitude_start": jdata.get("startLongitude", None),
            "latitude_end": jdata.get("endLatitude", None),
            "longitude_end": jdata.get("endLongitude", None),
            "calories": jdata.get("calories", None),
            "heartrate_avg": jdata.get("averageHR", None),
            "heartrate_max": jdata.get("maxHR", None),
            "heartrate_time_in_zone1": jdata.get("hrTimeInZone_1", None),
            "heartrate_time_in_zone2": jdata.get("hrTimeInZone_2", None),
            "heartrate_time_in_zone3": jdata.get("hrTimeInZone_3", None),
            "heartrate_time_in_zone4": jdata.get("hrTimeInZone_4", None),
            "heartrate_time_in_zone5": jdata.get("hrTimeInZone_5", None),
            "cycling_cadence_rpm_avg": jdata.get("averageBikingCadenceInRevPerMinute", None),
            "cycling_cadence_rpm_max": jdata.get("maxBikingCadenceInRevPerMinute", None),
            "laps": jdata.get("lapCount", None),
            "power_avg": jdata.get("avgPower", None),
            "power_max": jdata.get("maxPower", None),
            "power_norm": jdata.get("normPower", None),
            "power_max_avg_1sec": jdata.get("maxAvgPower_1", None),
            "power_max_avg_2sec": jdata.get("maxAvgPower_2", None),
            "power_max_avg_5sec": jdata.get("maxAvgPower_5", None),
            "power_max_avg_10sec": jdata.get("maxAvgPower_10", None),
            "power_max_avg_20sec": jdata.get("maxAvgPower_20", None),
            "power_max_avg_30sec": jdata.get("maxAvgPower_30", None),
            "power_max_avg_60sec": jdata.get("maxAvgPower_60", None),
            "power_max_avg_120sec": jdata.get("maxAvgPower_120", None),
            "power_max_avg_300sec": jdata.get("maxAvgPower_300", None),
            "power_max_avg_600sec": jdata.get("maxAvgPower_600", None),
            "power_max_avg_1200sec": jdata.get("maxAvgPower_1200", None),
            "power_max_avg_1800sec": jdata.get("maxAvgPower_1800", None),
            "power_time_in_zone1": jdata.get("powerTimeInZone_1", None),
            "power_time_in_zone2": jdata.get("powerTimeInZone_2", None),
            "power_time_in_zone3": jdata.get("powerTimeInZone_3", None),
            "power_time_in_zone4": jdata.get("powerTimeInZone_4", None),
            "power_time_in_zone5": jdata.get("powerTimeInZone_5", None),
            "power_time_in_zone6": jdata.get("powerTimeInZone_6", None),
            "power_time_in_zone7": jdata.get("powerTimeInZone_7", None),
            "sets_total": jdata.get("totalSets", None),
            "sets_active": jdata.get("activeSets", None),
            "reps_total": jdata.get("totalReps", None),
            "exercise_sets": exercise_sets,
        })
        entry.get_id() # <-- generate the object's ID string
        return entry

    @classmethod
    def sqlite3_fields_to_keep_visible(cls):
        return ["id", "time_start", "time_end", "activity_type", "event_type"]


# ============================= Database Objects ============================= #
# A configuration object for a database.
class GarminDatabaseConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("db_path",                  [str],      required=True),
        ]

# An object used to interact with a Garmin step database.
class GarminDatabase:
    def __init__(self, config: GarminDatabaseConfig):
        self.config = config
        self.table_steps_name = "steps"
        self.table_sleep_name = "sleep"
        self.table_vo2max_name = "vo2max"
        self.table_heart_rate_summary_name = "heart_rate_summary"
        self.table_heart_rate_name = "heart_rate"
        self.table_activity_name = "activities"

    # Determines if a table exists in the database.
    def table_exists(self, table: str) -> bool:
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        result = cur.execute("SELECT 1 FROM sqlite_master WHERE type == 'table' AND name == '%s';" % table)
        table_exists = result.fetchone() is not None
        con.close()
        return table_exists

    # Performs a search of the database and returns tuples in a list.
    def search(self, table: str, condition: str):
        # if the table doesn't exist, return an empty list
        if not self.table_exists(table):
            return []

        # build a SELECT command
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # connect, query, and return
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        result = cur.execute(cmd)
        return result

    # Executes a search using `ORDER BY` to retrieve entries without needing a
    # specific condition to identify them.
    def search_order_by(self,
                        table: str,
                        order_by_column: str,
                        desc: bool = False,
                        limit: int = None):
        # if the table doesn't exist, return an empty list
        if not self.table_exists(table):
            return []

        # build a SELECT command
        cmd = "SELECT * FROM %s ORDER BY %s" % (table, order_by_column)
        if desc:
            cmd += " DESC"
        if limit is not None:
            cmd += " LIMIT %d" % limit

        # connect, query, and return
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        result = cur.execute(cmd)
        return result

    # ------------------------------ Step Data ------------------------------- #
    # Inserts the provided entry into the database.
    def save_steps(self, entry: GarminDatabaseStepsEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseStepsEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_steps_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_steps_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_steps_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_steps_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple steps entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for step entries within the given time range.
    def search_steps_by_time_range(self, time_start: datetime, time_end: datetime):
        condition = "time_start >= %.f AND time_end <= %.f" % (
            time_start.timestamp(),
            time_end.timestamp()
        )
        result = self.search(self.table_steps_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest `time_end` timestamp, or `None` if
    # there are no entries.
    def search_steps_latest(self):
        result = self.search_order_by(
            self.table_steps_name,
            order_by_column="time_end",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseStepsEntry.from_sqlite3(row)
            return entry
        return None

    # ------------------------------ Step Data ------------------------------- #
    # Inserts the provided entry into the database.
    def save_sleep(self, entry: GarminDatabaseSleepEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseSleepEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_sleep_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_sleep_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_sleep_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_sleep_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple sleep entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for sleep entries within the given time range.
    def search_sleep_by_time_range(self, time_start: datetime, time_end: datetime):
        condition = "time_start >= %.f AND time_end <= %.f" % (
            time_start.timestamp(),
            time_end.timestamp()
        )
        result = self.search(self.table_sleep_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest `time_end` timestamp, or `None` if
    # there are no entries.
    def search_sleep_latest(self):
        result = self.search_order_by(
            self.table_sleep_name,
            order_by_column="time_end",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseSleepEntry.from_sqlite3(row)
            return entry
        return None

    # ----------------------------- VO2Max Data ------------------------------ #
    # Inserts the provided entry into the database.
    def save_vo2max(self, entry: GarminDatabaseVO2MaxEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseVO2MaxEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_vo2max_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_vo2max_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_vo2max_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_vo2max_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple vo2max entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for vo2max entries within the given time range.
    def search_vo2max_by_day(self, timestamp: datetime):
        condition = "timestamp >= %.f AND timestamp <= %.f" % (
            dtu.set_time_beginning_of_day(timestamp),
            dtu.set_time_end_of_day(timestamp),
        )
        result = self.search(self.table_vo2max_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest timestamp, or `None` if there are no
    # entries.
    def search_vo2max_latest(self):
        result = self.search_order_by(
            self.table_vo2max_name,
            order_by_column="timestamp",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseVO2MaxEntry.from_sqlite3(row)
            return entry
        return None

    # ----------------------- Heart Rate Summary Data ------------------------ #
    # Inserts the provided entry into the database.
    def save_heart_rate_summary(self, entry: GarminDatabaseHeartRateSummaryEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseHeartRateSummaryEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_heart_rate_summary_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_heart_rate_summary_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_heart_rate_summary_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_heart_rate_summary_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseHeartRateSummaryEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple heart rate summary entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for heart rate entries within the given time range.
    def search_heart_rate_summary_by_day(self, timestamp: datetime):
        condition = "timestamp >= %.f AND timestamp <= %.f" % (
            dtu.set_time_beginning_of_day(timestamp),
            dtu.set_time_end_of_day(timestamp),
        )
        result = self.search(self.table_heart_rate_summary_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseHeartRateSummaryEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest timestamp, or `None` if there are no
    # entries.
    def search_heart_rate_summary_latest(self):
        result = self.search_order_by(
            self.table_heart_rate_summary_name,
            order_by_column="timestamp",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseHeartRateSummaryEntry.from_sqlite3(row)
            return entry
        return None

    # --------------------------- Heart Rate Data ---------------------------- #
    # Inserts the provided entry into the database.
    def save_heart_rate(self, entry: GarminDatabaseHeartRateEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseHeartRateEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_heart_rate_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the steps entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_heart_rate_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    def save_heart_rate_bulk(self, entries: list[GarminDatabaseHeartRateEntry]):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseHeartRateEntry.sqlite3_fields_to_keep_visible()
        table_definition = entries[0].get_sqlite3_table_definition(
            self.table_heart_rate_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # build a long command that includes each entry provided
        cmd = "INSERT OR REPLACE INTO %s VALUES " % self.table_heart_rate_name
        entries_len = len(entries)
        for (i, entry) in enumerate(entries):
            sqlite3_str = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
            cmd += "%s" % str(sqlite3_str)

            # add a comma when necessary
            if i < entries_len - 1:
                cmd += ", "

        cur.execute(cmd)
        con.commit()
        con.close()

    # Searches for entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_heart_rate_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_heart_rate_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseHeartRateEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple heart rate entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for heart rate entries within the given time range.
    def search_heart_rate_by_day(self, timestamp: datetime):
        condition = "timestamp >= %.f AND timestamp <= %.f" % (
            dtu.set_time_beginning_of_day(timestamp),
            dtu.set_time_end_of_day(timestamp),
        )
        result = self.search(self.table_heart_rate_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseHeartRateEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest timestamp, or `None` if there are no
    # entries.
    def search_heart_rate_latest(self):
        result = self.search_order_by(
            self.table_heart_rate_name,
            order_by_column="timestamp",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseHeartRateEntry.from_sqlite3(row)
            return entry
        return None

    # ---------------------------- Activity Data ----------------------------- #
    # Inserts the provided entry into the database.
    def save_activity(self, entry: GarminDatabaseActivityEntry):
        # connect and make sure the table exists
        con = sqlite3.connect(self.config.db_path)
        cur = con.cursor()
        table_fields_kept_visible = GarminDatabaseActivityEntry.sqlite3_fields_to_keep_visible()
        table_definition = entry.get_sqlite3_table_definition(
            self.table_activity_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the entry into the database
        sqlite3_obj = entry.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_activity_name, str(sqlite3_obj)))
        con.commit()
        con.close()

    # Searches for step entries with the given entry ID.
    # Returns None if no entry was found, or the matching entry object.
    def search_activity_by_id(self, entry_id: str):
        condition = "id == '%s'" % entry_id
        result = self.search(self.table_activity_name, condition)

        # iterate through the returned entries and convert them to objects
        entry = None
        entry_count = 0
        for row in result:
            entry = GarminDatabaseActivityEntry.from_sqlite3_row(row)
            entry_count += 1

        # if we had more than one match, there is an issue with the database;
        # ID strings should be unique
        assert entry_count <= 1, \
               "Database error: multiple activity entries found with the same ID: \"%s\"" % \
               entry_id
        return entry

    # Searches for activity entries within the given time range.
    def search_activity_by_day(self, timestamp: datetime):
        condition = "time_start >= %.f AND time_start <= %.f" % (
            dtu.set_time_beginning_of_day(timestamp),
            dtu.set_time_end_of_day(timestamp),
        )
        result = self.search(self.table_activity_name, condition)

        # iterate through the returned entries and convert them to objects
        entries = []
        for row in result:
            entry = GarminDatabaseActivityEntry.from_sqlite3_row(row)
            entries.append(entry)
        return entries

    # Returns the entry with the latest timestamp, or `None` if there are no
    # entries.
    def search_activity_latest(self):
        result = self.search_order_by(
            self.table_activity_name,
            order_by_column="time_start",
            desc=True,
            limit=1,
        )
        for row in result:
            entry = GarminDatabaseActivityEntry.from_sqlite3(row)
            return entry
        return None

