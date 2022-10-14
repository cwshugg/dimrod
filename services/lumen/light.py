# Module that defines classes used to represent a single home light that lumen
# can interact with.

# Imports
import os
import sys
import enum


# ============================== Light Actions =============================== #
# Class that represents a single action supported by a light. The most common of
# these is "on" and "off", but some advanced lights support other actions, such
# as setting color and brightness.
class LightAction(enum.Enum):
    OFF = 0,
    ON = 1,
    COLOR = 2,
    BRIGHTNESS = 3,


# ================================== Lights ================================== #
# Class that represents a single light. The light has an identifier and a number
# of actions it supports through a remote API.
class Light:
    # Constructor. Takes in the light's ID and a number of flags indicating if
    # special features are present.
    def __init__(self, lid, has_color, has_brightness):
        self.lid = lid
        self.actions = []
        for a in actions:
            self.actions.append(a.strip().lower())
    
    # Turns the light on, optionally taking in color and brightness values.
    def turn_on(self, color=None, brightness=None):
        # TODO
        pass
    
    # Turns the light off.
    def turn_off(self):
        # TODO
        pass
    
