# This module implements various geolocation-based utility functions. "LU" is
# short for "Location Utilities".

# Imports
from datetime import datetime, timezone
import geopy.geocoders
import timezonefinder
import pytz
import requests
from dateutil import parser

# A class representing a location. Either latitude/longitude or an address may
# be provided.
class Location:
    # Constructor.
    def __init__(self, address: str = None,
                       latitude: float = None,
                       longitude: float = None):
        self.address = address
        self.latitude = latitude
        self.longitude = longitude

        # make sure one or the other was provided
        if not self.has_address():
            assert self.has_coordinates(), \
                   "If you did not provide an address for a Location, " \
                   "you must provide both a latitude AND longitude value."
        else:
            assert self.has_address(), \
                   "If you did not provide latitude and longitude values for " \
                   "a Location, you must provide an address."

    # Returns True if the location has an address.
    def has_address(self):
        return self.address is not None and len(self.address) > 0

    # Returns True if the location has a latitude and longitude coordinates.
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    # Returns either the object's internal latitude/longitude values, or
    # performs a lookup of the location's address to retrieve the correct
    # values.
    #
    # The coordinates are returned in an array like so:
    #
    #   [latitude: float, longitude: float]
    def get_coordinates(self):
        if self.has_coordinates():
            return [self.latitude, self.longitude]

        # perform an address lookup
        loc = geopy.geocoders.Nominatim(user_agent="dimrod").geocode(self.address)
        return [loc.latitude, loc.longitude]



# ============================= Helper Functions ============================= #
# By default, we'll use Raleigh, NC as the location.
LOCATION_DEFAULT = Location(latitude=35.786168069281715, longitude=-78.68165659384003)

# Gets and returns the appropriate timezone object for the provided location.
def get_timezone(loc: Location = None):
    if loc is None:
        loc = LOCATION_DEFAULT

    # get the location's coordinates, and use it to find the correct timezone
    [lat, lng] = loc.get_coordinates()
    tzname = timezonefinder.TimezoneFinder().timezone_at(lng=lng, lat=lat)
    tz = pytz.timezone(tzname)
    return tz

# Helper for the `get_sunrise()` and `get_sunset()` functions that retrieves
# and returns *both* the sunrise and sunset for the given datetime. A Location
# object can be specified; it defaults to Raleigh, NC.
#
# The sunrise and sunset are returned in an array like so:
#
#   [sunrise: datetime, sunset: datetime]
#
# If the API call fails, this function throws an exception.
def get_sunrise_sunset(loc: Location = None, dt: datetime = None):
    # if no location was provided, default to Raleigh
    if loc is None:
        loc = LOCATION_DEFAULT
    # if no datetime was provided, default to now
    if dt is None:
        dt = datetime.now()

    # use the latitude and longitude to determine the timezone to convert to
    [lat, lng] = loc.get_coordinates()
    tzname = timezonefinder.TimezoneFinder().timezone_at(lng=lng, lat=lat)
    tz = pytz.timezone(tzname)

    # build a JSON object to send to the API with the location and date
    dt_str = dt.strftime("%Y-%m-%d")
    payload = {
        "lat": lat,
        "lng": lng,
        "date": dt_str,
        "tzid": tzname
    }

    # send the request and retrieve the resulting JSON object
    r = requests.get("https://api.sunrise-sunset.org/json", params=payload)
    jdata = r.json()["results"]

    # parse sunrise
    sunrise_str = "%s %s" % (dt_str, jdata["sunrise"])
    sunrise = parser.parse(sunrise_str)
    sunrise = sunrise.replace(tzinfo=tz)

    # parse sunset
    sunset_str = "%s %s" % (dt_str, jdata["sunset"])
    sunset = parser.parse(sunset_str)
    sunset = sunset.replace(tzinfo=tz)

    # return both
    return [sunrise, sunset]

# Polls an online API to determine the sunrise on the given day, at the given
# location.
def get_sunrise(loc: Location = None, dt: datetime = None):
    return get_sunrise_sunset(loc=loc, dt=dt)[0]

# Polls an online API to determine the sunset on the given day, at the given
# location.
def get_sunset(loc: Location = None, dt: datetime = None):
    return get_sunrise_sunset(loc=loc, dt=dt)[1]

