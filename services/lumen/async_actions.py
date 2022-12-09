# This module defines a way for Lumen to fire off asynchronous events from its
# main thread. This is useful for a variety of asynchronous lighting procedures.

# Imports
import os
import sys
import threading
import time

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# ============================== Parent Classes ============================== #
# Function that takes in a string describing an async action and returns the
# matching class. Returns None on failure to find a match.
def type_to_action(typestr):
    mapping = {
        "onoff": AsyncOnOff,
        "on": AsyncOn,
        "off": AsyncOff
    }

    # convert the string to lowercase and search the mapping
    typestr = typestr.lower()
    if typestr not in mapping:
        return None
    return mapping[typestr]

# Parent config class.
class AsyncActionConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("light_id",         [str],      required=True),
            ConfigField("type",             [str],      required=True)
        ]

# Parent action class.
class AsyncAction(threading.Thread):
    # Constructor. Takes in a config JSON object.
    def __init__(self, lumen, config_data, config_class):
        threading.Thread.__init__(self, target=self.run)
        self.lumen = lumen
        self.config = config_class()
        self.config.parse_json(config_data)
    
    # The action's main runner function.
    def run(self):
        pass


# ================================ On Action ================================= #
class AsyncOnConfig(AsyncActionConfig):
    def __init__(self):
        super().__init__()
        fields = [
            ConfigField("color",            [list],     required=False,     default=None),
            ConfigField("brightness",       [float],    required=False,     default=None)
        ]
        self.fields += fields

class AsyncOn(AsyncAction):
    # Constructor.
    def __init__(self, lumen, config_data):
        super().__init__(lumen, config_data, AsyncOnConfig)
    
    # Thread main function.
    def run(self):
        self.lumen.power_on(self.config.light_id,
                            color=self.config.color,
                            brightness=self.config.brightness)


# ================================ Off Action ================================= #
class AsyncOffConfig(AsyncActionConfig):
    def __init__(self):
        super().__init__()
        fields = [
            ConfigField("color",            [list],     required=False,     default=None),
            ConfigField("brightness",       [float],    required=False,     default=None)
        ]
        self.fields += fields

class AsyncOff(AsyncAction):
    # Constructor.
    def __init__(self, lumen, config_data):
        super().__init__(lumen, config_data, AsyncOffConfig)
    
    # Thread main function.
    def run(self):
        self.lumen.power_off(self.config.light_id)


# ============================== On/Off Action =============================== #
class AsyncOnOffConfig(AsyncActionConfig):
    def __init__(self):
        super().__init__()
        fields = [
            ConfigField("timeout",          [int],      required=True),
            ConfigField("color",            [list],     required=False,     default=None),
            ConfigField("brightness",       [float],    required=False,     default=None)
        ]
        self.fields += fields

class AsyncOnOff(AsyncAction):
    # Constructor. Takes in config JSON object.
    def __init__(self, lumen, config_data):
        super().__init__(lumen, config_data, AsyncOnOffConfig)
    
    # Thread main function.
    def run(self):
        # power the light on, wait for the duration, then power it off
        self.lumen.power_on(self.config.light_id,
                            color=self.config.color,
                            brightness=self.config.brightness)
        time.sleep(self.config.timeout)
        self.lumen.power_off(self.config.light_id)

