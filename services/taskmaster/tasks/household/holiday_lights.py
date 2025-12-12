# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.dtu as dtu
import lib.lu as lu
from lib.oracle import OracleSession
from lumen.light import LightConfig, Light

# Service imports
from task import TaskConfig
from tasks.household.base import *

class TaskJob_Household_Holiday_Lights(TaskJob_Household):
    def init(self):
        # this is a very regular task we want to repeat, since it decides when
        # to turn on the lights during Halloween, Christmas, etc.
        # (this is in *seconds*)
        self.refresh_rate = 600

        # set a location from which we'll poll the sunset and sunrise (for now,
        # default to Raleigh, NC)
        self.home = lu.Location(latitude=35.786168069281715,
                                longitude=-78.68165659384003)

        # set windows within which lights can be turned on and off (this is in
        # *minutes*)
        self.sunrise_window = 30
        self.sunset_window = 30

    # Returns True if the given datetime is the holiday season.
    def is_holiday_season(self, dt: datetime):
        # if it's the last half of september through
        # november, we'll return true, so the halloween
        # lights can be turned on
        is_late_september = dt.month == 9 and dt.day >= 15
        if is_late_september:
            return True

        is_october = dt.month == 10
        if is_october:
            return True

        is_november = dt.month == 11
        if is_november:
            return True

        # otherwise, if it's December through early
        # January, return true, so the Christmas lights
        # can be turned on
        is_december = dt.month == 12
        if is_december:
            return True

        is_early_january = dt.month == 1 and dt.day <= 15
        if is_early_january:
            return True

        return False

    def update(self, todoist, gcal):
        # make sure we are in holiday times!
        now = datetime.now()
        if not self.is_holiday_season(now):
            return False

        # retrieve the sunrise/sunset times for home
        sunrise = now.replace(hour=6, minute=0, second=0)
        sunset = now.replace(hour=18, minute=0, second=0)
        try:
            [sunrise, sunset] = lu.get_sunrise_sunset(loc=self.home, dt=now)
        except Exception as e:
            self.log("Failed to retrieve sunrise/sunset times: %s" % e)
            return False

        # log the time and sunrise/sunset times
        log_messages = [
            "Now:                  %s (%d)" % \
            (now.strftime("%Y-%m-%d %H:%M:%S %p"), now.timestamp()),
            "Sunrise:              %s (%d)" % \
            (sunrise.strftime("%Y-%m-%d %H:%M:%S %p"), sunrise.timestamp()),
            "Sunset:               %s (%d)" % \
            (sunset.strftime("%Y-%m-%d %H:%M:%S %p"), sunset.timestamp())
        ]

        # determine how far away sunrise and sunset is
        sunrise_diff = int(abs(dtu.diff_in_minutes(now, sunrise)))
        sunset_diff = int(abs(dtu.diff_in_minutes(now, sunset)))
        log_messages.append("Minutes from sunrise: %d (window = %d)" %
                            (sunrise_diff, self.sunrise_window))
        log_messages.append("Minutes from sunset:  %d (window = %d)" %
                            (sunset_diff, self.sunset_window))

        # if we're in the sunrise window, we'll turn the lights off
        if sunrise_diff <= self.sunrise_window:
            lumen = self.service.get_lumen_session()

            # write all log messages
            for msg in log_messages:
                self.log(msg)
            self.log("Turning the holiday lights off...")

            self.toggle_lights(lumen, "off")
            return True

        # if we're in the sunrise window, we'll turn the lights on
        if sunset_diff <= self.sunset_window:
            lumen = self.service.get_lumen_session()

            # write all log messages
            for msg in log_messages:
                self.log(msg)
            self.log("Turning the holiday lights on...")

            self.toggle_lights(lumen, "on")
            return True

        # otherwise, there's nothing to do
        return False

    # Retrieves all lights tagged for holidays within Lumen.
    def get_all_holiday_lights(self, lumen: OracleSession):
        # ping lumen for all known lights
        r = lumen.get("/lights")
        lights = []
        ldata = lumen.get_response_json(r)
        for l in ldata:
            lconf = LightConfig()
            lconf.parse_json(l)
            light = Light(lconf)

            # does this light's tags match up with the holiday theme? If so,
            # add it to the list
            #
            # (but don't include the christmas tree)
            if light.match_tags("holiday") and \
               not (light.match_tags("tree") and light.match_tags("christmas")):
                lights.append(Light(lconf))

        return lights

    # Toggles lights on or off.
    def toggle_lights(self, lumen: OracleSession, action: str):
        action = action.strip().lower()
        assert action in ["on", "off"]

        # retrieve all lights from lumen that are tagged with "holiday"
        lights = self.get_all_holiday_lights(lumen)
        lights_len = len(lights)
        if lights_len == 0:
            self.log("There are no lights tagged with \"holiday\".")
            return

        self.log("Found %d lights tagged with \"holiday\"." % lights_len)

        # for each light, ping lumen and tell it to turn it on/off
        for light in lights:
            jdata = {"id": light.lid, "action": action}
            r = lumen.post("/toggle", payload=jdata)

            # check the response
            if r.status_code == 200 and lumen.get_response_success(r):
                self.log(" - Turned device \"%s\" to \"%s\"." % (light.lid, action))
            else:
                self.log(" - Failed to toggle device \"%s\"." % light.lid)

