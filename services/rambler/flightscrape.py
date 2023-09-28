# Implements a generic flight-scraping class.
# This is used by rambler to scrape flight prices from the internet.

# Imports
import os
import sys
import abc
import json
import requests

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Rambler imports
from configs import *


# ============================= Generic Objects ============================== #
# Represents a single flight scraped from online.
class FlightInfo:
    def __init__(self):
        # TODO
        pass

# Represents a generic scraper that must be overridden by a subclass.
class FlightScraper(abc.ABC):
    # Constructor.
    def __init__(self):
        pass
    
    # Scrapes the website for flight prices, given the parameters, and returns
    # a list of FlightInfo objects.
    @abc.abstractmethod
    def scrape(self, params: TripFlightConfig, timing: TripTimeConfig):
        pass


# ============================== Kayak Scraper =============================== #
class FlightScraper_Kayak(FlightScraper):
    # Constructor.
    def __init__(self):
        self.url = "https://www.kayak.com/flights"
    
    # Creates and returns a Kayak URL given the parameters.
    def make_url(self, params: TripFlightConfig, timing: TripTimeConfig):
        # TODO
        pass

    def scrape(self, params: TripFlightConfig, timing: TripTimeConfig):
        # TODO
        pass

