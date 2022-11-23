# This module defines class(es) representing a single network-connected device.

# Imports
import os
import sys
import json

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ============================== Device Config =============================== #
# Class that represents the required fields for a single Light object.
class DeviceConfig(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",             [str],      required=True),
            ConfigField("macaddr",          [str],      required=True)
        ]


# ================================= Devices ================================== #
# Class that represents a single light. The light has an identifier and a number
# of flags that 
class Device:
    # Constructor.
    def __init__(self, config: DeviceConfig):
        self.config = config
        self.config.macaddr = self.config.macaddr.lower()
    
    # Returns a string representation of the device.
    def __str__(self):
        return str(self.config)

