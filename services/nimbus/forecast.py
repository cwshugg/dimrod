# This module defines a way to represent a single location in the world.

# Imports
import os
import sys
import json
import dateutil.parser
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

class Forecast(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("name",                 [str],      required=True),
            ConfigField("description_short",    [str],      required=True),
            ConfigField("description_long",     [str],      required=True),
            ConfigField("temperature_value",    [int],      required=True),
            ConfigField("temperature_unit",     [str],      required=True),
            ConfigField("wind_speed",           [str],      required=True),
            ConfigField("wind_direction",       [str],      required=True),
            ConfigField("time_start",           [str],      required=True),
            ConfigField("time_end",             [str],      required=True)
        ]

    # Overridden JSON parsing function.
    def parse_json(self, jdata: dict):
        # the API returns slightly different names, so I'll rename some
        # fields here
        renames = {
            "startTime": "time_start",
            "endTime": "time_end",
            "temperature": "temperature_value",
            "temperatureUnit": "temperature_unit",
            "windSpeed": "wind_speed",
            "windDirection": "wind_direction",
            "shortForecast": "description_short",
            "detailedForecast": "description_long"
        }
        for oldname in renames:
            newname = renames[oldname]
            jdata[newname] = jdata.pop(oldname)
        
        # run the original JSON parsing function
        super().parse_json(jdata)

        # convert the start and ending times from strings to datetime objects
        self.time_start = dateutil.parser.parse(self.time_start)
        self.time_end = dateutil.parser.parse(self.time_end)
    
    # Overridden JSON conversion function.
    def to_json(self):
        jdata = super().to_json()
        jdata["time_start"] = self.time_start.timestamp()
        jdata["time_end"] = self.time_end.timestamp()
        return jdata

