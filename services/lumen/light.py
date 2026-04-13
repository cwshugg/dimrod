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
class LightConfig(Config):
    """Class that represents the required fields for a single Light object."""
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=True),
            ConfigField("description",      [str],      required=True),
            ConfigField("has_color",        [bool],     required=True),
            ConfigField("has_brightness",   [bool],     required=True),
            ConfigField("tags",             [list],     required=False,     default=[])
        ]


# ================================== Lights ================================== #
class Light:
    """Class that represents a single light. The light has an identifier and a number
    of flags that
    """
    def __init__(self, config: LightConfig):
        """Constructor. Takes in the light's ID and a number of flags indicating if
        special features are present.
        """
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

    def __str__(self):
        """Creates a string representation of the Light object."""
        return "%s [has color: %s] [has brightness: %s]" % \
               (self.lid, self.has_color, self.has_brightness)

    def to_json(self):
        """Builds a JSON dictionary containing the class fields and returns it."""
        return {
            "id": self.lid,
            "description": self.description,
            "tags": self.tags,
            "has_color": self.has_color,
            "has_brightness": self.has_brightness,
            "status": self.status
        }

    def match_id(self, text: str):
        """Uses the light's name to match text. Returns True if the name contains the
        given text.
        """
        return text.lower() in self.lid.lower()

    def match_tags(self, text: str):
        """Uses the light's tags to match text. Returns True if the tags contain the
        given text.
        """
        tl = text.lower()
        for tag in self.tags:
            if tl in tag.lower():
                return True
        return False

    # ------------------------ Thread Synchronization ------------------------ #
    def lock(self):
        """Acquire's the light's internal lock (blocking if necessary)."""
        self.thread_lock.acquire()

    def unlock(self):
        """Releases the light's internal lock."""
        self.thread_lock.release()

    # -------------------------- Status Operations --------------------------- #
    def get_power(self):
        """Retrieves the last-known status of the light's power."""
        return self.status["power"]

    def get_color(self):
        """Retrieves the last-known status of the light's color, or None if the light
        doesn't have color.
        """
        return self.status["color"] if self.has_color else None

    def get_brightness(self):
        """Retrieves the last-known status of the light's brightness, or None if the
        light doesn't have brightness.
        """
        return self.status["brightness"] if self.has_brightness else None

    def set_power(self, power: bool):
        """Set's the light's power status."""
        self.status["power"] = power

    def set_color(self, color: str):
        """Set's the light's color status."""
        self.status["color"] = color

    def set_brightness(self, brightness: float):
        """Set's the light's brightness status."""
        self.status["brightness"] = brightness

