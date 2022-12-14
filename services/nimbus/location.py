# This module defines a way to represent a single location in the world.

# Imports
import os
import sys
import json
from geopy.geocoders import get_geocoder_for_service

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

class Location(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("name",                 [str],      required=False,     default=""),
            ConfigField("address",              [str],      required=False,     default=None),
            ConfigField("longitude",            [float],    required=False,     default=None),
            ConfigField("latitude",             [float],    required=False,     default=None)
        ]

    # Creates and returns a string representation of the object.
    def __str__(self):
        result = "%s " % self.name if self.name is not None else ""
        result += "[address: %s] " % self.address
        result += "[longitude: %s] " % self.longitude
        result += "[latitude: %s] " % self.latitude
        return result

    # Overridden JSON parsing function.
    def parse_json(self, jdata: dict):
        super().parse_json(jdata)

        # check for coordinates and address
        has_coords = self.longitude is not None and \
                     self.latitude is not None
        has_address = self.address is not None
        assert has_coords or has_address, \
               "either a longitude/latitude or an address must be given"
    

    # ----------------------------- Geolocation ------------------------------ #
    # Calls either 'self.locate_by_address' or 'self.locate_by_coordinates'
    # depending on what the object contains.
    def locate(self, user_agent="private_app_nimbus"):
        if self.longitude is not None and self.latitude is not None:
            return self.locate_by_coordinates(user_agent=user_agent)
        return self.locate_by_address(user_agent=user_agent)
    
    # Uses geopy to locate the location based on its address.
    def locate_by_address(self, geocoder="nominatim", user_agent="private_app_nimbus"):
        assert self.address is not None, "the location's address is not set"

        # create a geolocator and look up the address
        locator_class = get_geocoder_for_service(geocoder)
        locator = locator_class(user_agent=user_agent)
        result = locator.geocode(self.address)

        # if a result was found, update the location's coordinates
        if result is not None:
            self.longitude = result.longitude
            self.latitude = result.latitude
        return result
    
    # Uses geopy to locate the location based on its coordinates.
    def locate_by_coordinates(self, geocoder="nominatim", user_agent="private_app_nimbus"):
        assert self.longitude is not None and self.latitude is not None, \
               "the location's longitude and/or latitude is not set"

        # create a geolocator and look up the coordinates
        locator_class = get_geocoder_for_service(geocoder)
        locator = locator_class(user_agent=user_agent)
        result = locator.reverse("%f, %f" % (self.longitude, self.latitude))

        # if a result was found, update the location's address
        if result is not None:
            self.address = result.address
        return result

