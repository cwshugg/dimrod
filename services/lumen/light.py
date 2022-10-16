# Module that defines classes used to represent a single home light that lumen
# can interact with.

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


# =============================== Light Config =============================== #
# Class that represents the required fields for a single Light object.
class LightConfig(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=True),
            ConfigField("has_color",        [bool],     required=True),
            ConfigField("has_brightness",   [bool],     required=True)
        ]


# ================================== Lights ================================== #
# Class that represents a single light. The light has an identifier and a number
# of flags that 
class Light:
    # Constructor. Takes in the light's ID and a number of flags indicating if
    # special features are present.
    def __init__(self, lid, has_color, has_brightness):
        self.lid = lid
        self.has_color = has_color
        self.has_brightness = has_brightness
    
    # Creates a string representation of the Light object.
    def __str__(self):
        return "%s [has color: %s] [has brightness: %s]" % \
               (self.lid, self.has_color, self.has_brightness)
    
    # Builds a JSON dictionary containing the class fields and returns it.
    def to_json(self):
        return {
            "id": self.lid,
            "has_color": self.has_color,
            "has_brightness": self.has_brightness
        }
    
