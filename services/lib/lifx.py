# This module implements a light wrapper around the LIFX LAN library. This
# allows for the interaction with LIFX devices on the LAN (Local Area Network).

# Imports
import os
import sys
from datetime import datetime
import time
import colorsys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# LIFX imports
from lifxlan import LifxLAN, Light

# An object used to configure the LIFX object.
class LIFXConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("refresh_delay",    [int],  required=False, default=1800),
            ConfigField("retry_attempts",   [int],  required=False, default=4),
            ConfigField("retry_delay",      [int],  required=False, default=1)
        ]

# The wrapper around the LIFX LAN SDK.
class LIFX:
    # Constructor.
    def __init__(self, config: LIFXConfig):
        self.lifx = LifxLAN()
        self.config = config

        self.lights = None
        self.last_refresh = None
    
    # Takes in an error, resets the LifxLAN object (for future calls to use a
    # fresh instance, in case this helps avoid unexpected errors), then throws
    # the error.
    def handle_error(self, err):
        self.lifx = LifxLAN()
        raise err
    
    # Retrieves and returns a list of online light objects.
    def get_lights(self, refresh=False):
        # we only want to perform the LAN search if we've reached out refresh
        # time, or if the caller forces our hand
        now = datetime.now()
        if refresh or \
           self.last_refresh is None or \
           dtu.diff_in_seconds(now, self.last_refresh) > self.config.refresh_delay:
            
            err = None
            for i in range(self.config.retry_attempts):
                try:
                    # retrieve all lights and return them
                    self.lights = self.lifx.get_lights()
                    self.last_refresh = now
                    return self.lights
                except Exception as e:
                    err = e
                    time.sleep(self.config.retry_delay)
            self.handle_error(err)

        return self.lights
    
    # Attempts to retrieve and find a light by its name. Returns the matching
    # object, or None.
    def get_light_by_name(self, name: str):
        query = name.strip()

        err = None
        for i in range(self.config.retry_attempts):
            try:
                # retrieve the list of lights, then iterate through them and
                # search for a light with a matching name
                lights = self.get_lights()
                for l in lights:
                    if l.get_label().strip() == query:
                        return l
                return None
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        self.handle_error(err)

    # Attempts to retrieve and find a light by its MAC and IP addresses.
    # Returns the matching object, or None.
    def get_light_by_address(self, macaddr: str, ipaddr: str):
        err = None
        for i in range(self.config.retry_attempts):
            try:
                # create a light object directly, using the given MAC and IP
                # addresses
                return Light(macaddr, ipaddr)
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        self.handle_error(err)
    
    # Toggles a light with the given fields.
    def set_light_power(self, light: Light, action: str):
        action = action.strip().lower()
        assert action in ["on", "off"]

        err = None
        for i in range(self.config.retry_attempts):
            try:
                # turn the light on or off, depending on the provided action
                light.set_power(action, rapid=True)
                return
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        self.handle_error(err)

    def set_light_color(self, light: Light, color):
        # LIFX LAN accepts color as a list of:
        #
        #   [
        #       hue (0-65535),
        #       saturation (0-65535),
        #       brightness (0-65535),
        #       kelvn (2500-9000)
        #   ]
        #
        # We need to convert the RGB and brightness to these values.

        # normalize the RGB values such that they are on a [0.0, 1.0] scale,
        # then use them to convert to HSV/HSB
        r = color[0] / 255.0
        g = color[1] / 255.0
        b = color[2] / 255.0
        hsv = list(colorsys.rgb_to_hsv(color[0], color[1], color[2]))
        
        # scale all three values to the scale of [0, 65535]
        hsv[0] = hsv[0] * 65535.0
        hsv[1] = hsv[1] * 65535.0
        hsv[2] = (hsv[2] * 65535.0) / 255.0

        # finally, set the kelvin value (currently setting this to zero and the
        # API seems to be taking care of things), then form the final array to
        # pass to the LIFX object
        kelvin = 0.0
        newcolors = hsv + [kelvin]
            
        err = None
        for i in range(self.config.retry_attempts):
            try:
                # apply the change
                light.set_color(newcolors, rapid=True)
                return
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        self.handle_error(err)

