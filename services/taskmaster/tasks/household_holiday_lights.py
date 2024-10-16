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
from task import TaskConfig
from tasks.base import *
import lib.dtu as dtu

class TaskJob_Household_Holiday_Lights(TaskJob_Household):
    def init(self):
        # this is a very regular task we want to repeat, since it decides when
        # to turn on the lights during Halloween, Christmas, etc.
        self.refresh_rate = 600

        # set a location from which we'll poll the sunset and sunrise (for now,
        # default to Raleigh, NC)
        self.home = lu.Location(latitude=35.786168069281715,
                                longitude=-78.68165659384003)
    
        # set windows within which lights can be turned on and off
        self.sunrise_window = 1800
        self.sunset_window = 1800

    def update(self, todoist, gcal):
        # retrieve the current timestamp, and the sunrise/sunset times for home
        now = datetime.now()
        sunrise = now.replace(hour=6, minute=0, second=0)
        sunset = now.replace(hour=18, minute=0, second=0)
        try:
            [sunrise, sunset] = lu.get_sunrise_sunset(loc=loc, dt=now)
        except Exception as e:
            self.log("Failed to retrieve sunrise/sunset times: %s" % e)
            return False
        
        # log the time and sunrise/sunset times
        self.log("Now:                  %s (%d)" % (now.strftime("%Y-%m-%d %H:%M:%S %p"), now.timestamp()))
        self.log("Sunrise:              %s (%d)" % (sunrise.strftime("%Y-%m-%d %H:%M:%S %p"), sunrise.timestamp()))
        self.log("Sunset:               %s (%d)" % (sunset.strftime("%Y-%m-%d %H:%M:%S %p"), sunset.timestamp()))

        # determine how far away sunrise and sunset is
        sunrise_diff = dtu.diff_in_minutes(now, sunrise)
        sunset_diff = dtu.diff_in_minutes(now, sunset)
        self.log("Minutes from sunrise: %d" % sunrise_diff)
        self.log("Minutes from sunse:   %d" % sunse_diff)

        if sunrise_diff < self.sunrise_window:
            self.toggle_lights("off")
            return True

        if sunset_diff < self.sunset_window:
            self.toggle_lights("on")
            return True
        
        return False
    
    # Retrieves all lights tagged for holidays within Lumen.
    def get_all_holiday_lights(self):
        # TODO - get lumen session and retrieve all lights. Returns a list of
        # Light objects (or just Light ID strings) that are all tagged with
        # "holiday"
        return []
    
    # Toggles lights on or off.
    def toggle_lights(self, action: str):
        action = action.strip().lower()
        assert action in ["on", "off"]

        lights = self.get_all_holiday_lights()
        # TODO - get Lumen session and send requests for all lights

