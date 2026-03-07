# Module that defines a configuration class for a service or some other object.
# This class inherits much from the `Uniserdes` object (universal
# serializer/deserializer).
#
#   Connor Shugg

# Imports
import os
import sys
import json
from datetime import datetime
from enum import Enum

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField

# Represents a single config file field.
class ConfigField(UniserdesField):
    def __init__(self, name, types, required=False, default=None):
        super().__init__(name, types, required=required, default=default)


# Config class.
class Config(Uniserdes):
    def __init__(self):
        super().__init__()

