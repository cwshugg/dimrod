# Module that defines classes used to represent a single home light that lumen
# can interact with.

# Imports
import os
import sys
import json
import threading

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
            ConfigField("description",      [str],      required=True),
            ConfigField("has_color",        [bool],     required=True),
            ConfigField("has_brightness",   [bool],     required=True),
            ConfigField("tags",             [list],     required=False,     default=[])
        ]


# ================================== Lights ================================== #
# Class that represents a single light. The light has an identifier and a number
# of flags that
class Light:
    # Constructor. Takes in the light's ID and a number of flags indicating if
    # special features are present.
    def __init__(self, config: LightConfig):
        self.config = config
        self.lid = self.config.id
        self.description = self.config.description
        self.has_color = self.config.has_color
        self.has_brightness = self.config.has_brightness
        self.tags = self.config.tags

        # each light has a lock to prevent two lumen action threads from
        # modifying the same light simultaneously
        self.thread_lock = threading.Lock()

        # internal light status trackers
        self.status = {"power": False, "color": "255,255,255", "brightness": 1.0}

    # Creates a string representation of the Light object.
    def __str__(self):
        return "%s [has color: %s] [has brightness: %s]" % \
               (self.lid, self.has_color, self.has_brightness)

    # Builds a JSON dictionary containing the class fields and returns it.
    def to_json(self):
        return {
            "id": self.lid,
            "description": self.description,
            "tags": self.tags,
            "has_color": self.has_color,
            "has_brightness": self.has_brightness,
            "status": self.status
        }

    # Uses the light's name to match text. Returns True if the name contains the
    # given text.
    def match_id(self, text: str):
        return text.lower() in self.lid.lower()

    # Uses the light's tags to match text. Returns True if the tags contain the
    # given text.
    def match_tags(self, text: str):
        tl = text.lower()
        for tag in self.tags:
            if tl in tag.lower():
                return True
        return False

    # ------------------------ Thread Synchronization ------------------------ #
    # Acquire's the light's internal lock (blocking if necessary).
    def lock(self):
        self.thread_lock.acquire()

    # Releases the light's internal lock.
    def unlock(self):
        self.thread_lock.release()

    # -------------------------- Status Operations --------------------------- #
    # Retrieves the last-known status of the light's power.
    def get_power(self):
        return self.status["power"]

    # Retrieves the last-known status of the light's color, or None if the light
    # doesn't have color.
    def get_color(self):
        return self.status["color"] if self.has_color else None

    # Retrieves the last-known status of the light's brightness, or None if the
    # light doesn't have brightness.
    def get_brightness(self):
        return self.status["brightness"] if self.has_brightness else None

    # Set's the light's power status.
    def set_power(self, power: bool):
        self.status["power"] = power

    # Set's the light's color status.
    def set_color(self, color: str):
        self.status["color"] = color

    # Set's the light's brightness status.
    def set_brightness(self, brightness: float):
        self.status["brightness"] = brightness

