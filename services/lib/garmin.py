# This module implements code to interact with Garmin Connect.

# Imports
import os
import sys
import enum
from datetime import datetime
import logging

# Garmin imports
import garminconnect
from garth.exc import GarthHTTPError

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Silence logging from garminconnect
logging.getLogger("garminconnect").setLevel(logging.CRITICAL)

# Default directory to store Garmin Connect auth tokens
garminconnect_default_token_dir = os.path.join(os.path.expandvars("${HOME}"), ".garminconnect")

# An enum used to represent the various states of logging in with Garmin
# Connect. This can get tricky due to 2FA requirements, so this helps clarify
# things for the caller.
class GarminLoginStatus(enum.Enum):
    SUCCESS = 0,
    FAILURE = 1,
    BAD_CREDENTIALS = 2,
    NEED_2FA = 3,
    RATE_LIMITED = 4,
    BAD_2FA_CODE = 5,


# ============================= Main API Objects ============================= #
# A config object used to configure the `Garmin` object.
class GarminConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("account_email",            [str],  required=True),
            ConfigField("account_password",         [str],  required=True),
            ConfigField("auth_2fa_telegram_chat",   [str],  required=True),
            ConfigField("auth_tokenstore_dir",      [str],  required=False, default=garminconnect_default_token_dir),
        ]

# Main class for interacting with Garmin Connect.
class Garmin:
    def __init__(self, config: GarminConfig):
        self.config = config
        self.client = None
        self.login_with_2fa_data = None

    # ---------------------------- Authentication ---------------------------- #
    # Logs in using the configured email and password credentials.
    # Returns a login status code.
    def login_with_credentials(self):
        try:
            client = garminconnect.Garmin(
                self.config.account_email,
                self.config.account_password,
                return_on_mfa=True,
            )
            (status, intermediate_data) = client.login()

            # if 2FA is needed, return early and save the intermediate login
            # data for later (after we get the 2FA code). The caller should
            # find a way to get the 2FA code from the user, then invoke
            # `login_with_2fa`.
            if status.strip().lower() == "needs_mfa":
                self.login_with_2fa_data = intermediate_data
                self.client = client
                return GarminLoginStatus.NEED_2FA

            # on success, dump the new token for later use, and save the client
            # for future API calls
            client.garth.dump(self.config.auth_tokenstore_dir)
            self.client = client
            return GarminLoginStatus.SUCCESS
        except garminconnect.GarminConnectAuthenticationError as e:
            return GarminLoginStatus.BAD_CREDENTIALS
        except Exception as e:
            return GarminLoginStatus.FAILURE

    # Attempts to log in with the provided
    def login_with_2fa(self, code: str):
        # make sure we have the 2FA data and the client object from the initial
        # credentials-based login step
        assert self.login_with_2fa_data is not None and \
               self.client is not None, \
               "Intermediate login data not available; you must call `login_with_credentials` first"

        # attempt to resume the login using the 2FA code. Handle Garth-specific
        # errors accordingly
        try:
            self.client.resume_login(self.login_with_2fa_data, code)

            # on success, dump the new token for later use
            self.client.garth.dump(self.config.auth_tokenstore_dir)
            return GarminLoginStatus.SUCCESS
        except GarthHTTPError as e:
            e_str = str(e).strip().lower()
            if "429" in e_str:
                return GarminLoginStatus.RATE_LIMITED
            elif "401" in e_str:
                return GarminLoginStatus.BAD_2FA_CODE
            else:
                return GarminLoginStatus.FAILURE

    # Attempts to log in with an existing token.
    def login_with_tokenstore(self):
        try:
            client = garminconnect.Garmin()
            client.login(self.config.auth_tokenstore_dir)
            self.client = client
            return GarminLoginStatus.SUCCESS
        except Exception as e:
            return GarminLoginStatus.FAILURE

    # Helper function for ensuring the client is logged in.
    def check_logged_in(self):
        assert self.client is not None, \
            "Not logged into Garmin Connect; please call one of the login methods first"

    # ------------------------------- Helpers -------------------------------- #
    # Helper function (mostly for debugging) that returns a list of all
    # functions that can be invoked through the inner Garmin client.
    def get_all_inner_functions(self):
        self.check_logged_in()
        results = []
        for entry in dir(self.client):
            if callable(getattr(self.client, entry)) and not entry.startswith("_"):
                results.append(entry)
        return results

    # Generic function to help check for errors in returned Garmin API results.
    # The provided result object is passed through this function and returned.
    def check_errors(self, result):
        # look for an error message
        errmsg = None
        if hasattr(result, "errorMessage") and result.errorMessage is not None:
            errmsg = str(result.errorMessage)

        # look for a status code
        status_code = None
        if hasattr(result, "statusCode"):
            status_code = result.statusCodea
            if status_code == 200:
                return result

            # put together an error message
            msg = "Garmin API error (code %d)" % status_code
            if errmsg is not None:
                msg += ": %s" % errmsg
            raise Exception(msg)

        return result


    # ------------------------------ Interface ------------------------------- #
    # Returns the full name of the Garmin account owner.
    def get_full_name(self):
        self.check_logged_in()
        return self.check_errors(self.client.get_full_name())

    # Returns an object containing data on the device that was used last.
    def get_device_last_used(self):
        self.check_logged_in()
        return self.check_errors(self.client.get_device_last_used())

    # Returns a list of objects containing device data.
    def get_devices(self):
        self.check_logged_in()
        return self.check_errors(self.client.get_devices())

    # Returns a list of objects containing step data; one object for each day
    # in the specified range.
    def get_steps_per_day(self, start_date: datetime, end_date: datetime):
        self.check_logged_in()
        return self.check_errors(self.client.get_daily_steps(
            dtu.format_yyyymmdd(start_date),
            dtu.format_yyyymmdd(end_date),
        ))

    # Returns a list of objects containing data on the number of floors/stories
    # climbed.
    # NOTE: This function calls the Garmin API once per day in the specified
    # range, so it may take a while to complete if the range is large. It may
    # also quickly hit the rate limit if you requeset too many.
    def get_floors_per_day(self, start_date: datetime, end_date: datetime):
        self.check_logged_in()
        days = dtu.split_by_day(start_date, end_date)

        results = []
        for day in days:
            results.append(self.check_errors(self.client.get_floors(dtu.format_yyyymmdd(day))))
        return results

    # Returns a list of activities for a specific day.
    def get_activities_for_day(self, dt: datetime):
        self.check_logged_in()
        result = self.client.get_activities_fordate(dtu.format_yyyymmdd(dt))
        self.check_errors(result)

        # Extract the activity object list from the result object
        activities = result["ActivitiesForDay"]["payload"]
        return activities

    # Returns a list of activities across a span of days.
    # The returned list is in sorted order, where the earliest activity
    # appears first in the list
    def get_activities_for_day_range(self, start_date: datetime, end_date: datetime):
        self.check_logged_in()
        return self.check_errors(self.client.get_activities_by_date(
            dtu.format_yyyymmdd(start_date),
            dtu.format_yyyymmdd(end_date),
            sortorder="asc"
        ))

    # Returns heart rate data for the given date.
    def get_heart_rate_for_day(self, dt: datetime):
        self.check_logged_in()
        return self.check_errors(self.client.get_heart_rates(dtu.format_yyyymmdd(dt)))

    # Returns sleep data for the given date.
    def get_sleep_for_day(self, dt: datetime):
        self.check_logged_in()
        return self.check_errors(self.client.get_sleep_data(dtu.format_yyyymmdd(dt)))


# ================================ TEST CODE ================================= #
#import lib.dtu as dtu
#from datetime import datetime
#import json
#
#config = GarminConfig.from_json({
#    "account_email": os.getenv("GARMIN_EMAIL", ""),
#    "account_password": os.getenv("GARMIN_PASSWORD", ""),
#})
#g = Garmin(config)
#
#lwt = g.login_with_tokenstore()
#print("LOGIN WITH TOKEN STORE: %s" % str(lwt))
#
#if lwt != GarminLoginStatus.SUCCESS:
#    lwc = g.login_with_credentials()
#    print("LOGIN WITH CREDENTIALS: %s" % str(lwc))
#    if lwc == GarminLoginStatus.NEED_2FA:
#        code = input("Enter 2FA code: ")
#        lw2 = g.login_with_2fa(code)
#        print("LOGIN WITH 2FA: %s" % str(lw2))
#
## SHOW ALL FUNCTIONS
#print("ALL INNER API FUNCTIONS:")
#for entry in g.get_all_inner_functions():
#    print("  - %s" % entry)
#
#device_info = g.get_device_last_used()
#print("DEVICE LAST USED: %s" % device_info)
#
#devices = g.get_devices()
#print("DEVICES:")
#for device in devices:
#    print("  - %s (%s)" % (device.get("displayName", "???"), device.get("deviceId", "???")))
#
#now = datetime.now()
#steps_start = dtu.add_weeks(now, -1)
#steps_end = now
#steps = g.get_steps_per_day(steps_start, steps_end)
#print("STEPS FROM %s TO %s:" % (dtu.format_yyyymmdd(steps_start), dtu.format_yyyymmdd(steps_end)))
#for day in steps:
#    print("  - %s: %d" % (day["calendarDate"], day["totalSteps"]))
#
##floors_start = steps_start
##floors_end = steps_end
##floors = g.get_floors_per_day(floors_start, floors_end)
##print("FLOORS FROM %s TO %s:" % (dtu.format_yyyymmdd(steps_start), dtu.format_yyyymmdd(steps_end)))
##for floor in floors:
##    print("  - %s: ascended %d, descended %d" % (floor["calendarDate"], floor["totalFloors"]))
#
#activities = g.get_activities_for_day(dtu.add_days(steps_start, 4))
#print("ACTIVITIES: %s" % activities)
#
#activities = g.get_activities_for_day_range(steps_start, steps_end)
#print("ACTIVITIES (%s - %s): %s" % (steps_start, steps_end, activities))
#
#hr = g.get_heart_rate_for_day(dtu.add_days(dtu.add_days(steps_end, -1)))
#print("HEART RATE DATA: %s" % hr)
#
#hr = g.get_sleep_for_day(dtu.add_days(dtu.add_days(steps_end, -1)))
#print("SLEEP DATA: %s" % json.dumps(hr, indent=4))

