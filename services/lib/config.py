# Module that defines a configuration class for a service or some other object.
#
# This class inherits much from the `Uniserdes` object (universal
# serializer/deserializer), but it's intented to be used for *configuring*
# something, not simply as a way to represent arbitrary data. (For that
# purpose, the base `Uniserdes` class should be used instead.)
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

class ConfigField(UniserdesField):
    """Represents a single config file field."""
    def __init__(self, name, types, required=False, default=None):
        super().__init__(name, types, required=required, default=default)


class Config(Uniserdes):
    """Config class."""
    def __init__(self):
        super().__init__()

