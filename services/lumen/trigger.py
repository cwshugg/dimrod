# This module defines a way to specify certain events that will trigger an action.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Service imports
from async_actions import type_to_action, AsyncActionConfig


# ================== Parent Trigger Class and Type Mapping =================== #
# Takes in a string and attempts to return the corresponding Trigger class.
# Returns None if no such match exists.
def type_to_trigger(typestr):
    mapping = {
        "device_connect": DeviceConnectTrigger,
        "daily_timed": DailyTimedTrigger
    }

    # convert the string to lowercase and search the mapping
    typestr = typestr.lower()
    if typestr not in mapping:
        return None
    return mapping[typestr]

# Config class.
class Trigger(Config):
    def __init__(self, lumen):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("type",         [str],      required=True),
            ConfigField("actions",      [list],     required=True)
        ]
        self.lumen = lumen
    
    # Overridden version of self.prase_json().
    def parse_json(self, jdata):
        super().parse_json(jdata)
        
        actions = []
        for adata in self.actions:
            # parse the actions as a generic AsyncAction
            aconf = AsyncActionConfig()
            aconf.parse_json(adata)

            # then, parse again as the specific action, based on the type
            aclass = type_to_action(aconf.type)
            assert aclass is not None, "unknown action type: \"%s\"" % aconf.type
            action_data = {
                "class": aclass,
                "data": adata
            }
            actions.append(action_data)
        self.actions = actions
    
    # Used to fire off the trigger's actions.
    def fire(self):
        # fire each individual action
        for adata in self.actions:
            action = adata["class"](self.lumen, adata["data"])
            action.start()
    
    # Returns whether or not the trigger is ready. This may or may not be
    # implemented by subclasses.
    def is_ready(self):
        return False


# ========================== Device-Connect Trigger ========================== #
class DeviceConnectTrigger(Trigger):
    def __init__(self, lumen):
        super().__init__(lumen)
        self.fields += [
            ConfigField("macaddr",              [str],      required=True),
        ]


# ========================== "Daily Timed Triggers" ========================== #
class DailyTimedTrigger(Trigger):
    def __init__(self, lumen):
        super().__init__(lumen)
        self.fields += [
            ConfigField("hour",                 [int],      required=True),
            ConfigField("minute",               [int],      required=True),
            ConfigField("minute_slack",         [float],    required=False,     default=0.5)
        ]

    # Overridden 'is_ready' function.
    def is_ready(self):
        # check the hour and minute of the day (give the minute-checking some
        # slack)
        now = datetime.now()
        same_hour = now.hour == self.hour
        same_minute = now.minute >= (self.minute - self.minute_slack) and \
                      now.minute <= (self.minute + self.minute_slack)
        return same_hour and same_minute

