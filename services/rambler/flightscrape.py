# Implements a generic flight-scraping class.
# This is used by rambler to scrape flight prices from the internet.

# Imports
import os
import sys
import abc
import json
import re

# Selenium imports
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ExpectedCond
from selenium.webdriver.common.by import By

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
    def __init__(self, config):
        self.config = config
        self.browser = None
    
    # Scrapes the website for flight prices, given the parameters, and returns
    # a list of FlightInfo objects.
    # The 'count' field restricts the number of results returned.
    @abc.abstractmethod
    def scrape(self, params: TripFlightConfig,
               dt_embark: datetime,
               dt_return: datetime,
               count=10):
        pass
    
    # Creates and returns a selenium browser object.
    def browser_open(self):
        assert self.browser is None, "A browser already exists."
        # pass a number of arguments into firefox
        opts = webdriver.FirefoxOptions()
        opts.add_argument("-headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        self.browser = webdriver.Firefox(options=opts)
    
    # Closes the current webdriver/browser instance.
    def browser_exit(self):
        assert self.browser is not None, "No browser has been opened."
        self.browser.quit()
        self.browser = None
    
#    # Attempts to parse and extract JSON dictionaries from raw text.
#    def extract_json(self, txt: str):
#        results = []
#
#        # iterate through the text, character by character, while maintaining
#        # knowledge of how many brackets and quotes we are nested in during the
#        # iteration
#        quote_stack = []
#        i = 0
#        txt_len = len(txt)
#        current_json_start = -1
#        current_json_quote_nest = -1
#        current_json_bracket_count = 0
#        while i < txt_len:
#            c = txt[i]
#
#            # if the current character is a quote, update the quote stack
#            qcs = ["\"", "'"]
#            for qc in qcs:
#                # make sure the current character is a quote, and that it's not
#                # an escape-sequenced quote
#                if c != qc or (i > 0 and txt[i - 1] == "\\"):
#                    continue
#                # if the quote stack is empty, OR the latest value on the quote
#                # stack doesn't match, , this must be a new quote pair
#                if len(quote_stack) == 0 or quote_stack[-1] != c:
#                    quote_stack.append(c)
#                # otherwise, if the last-pushed character is the same as the
#                # current quote character, this must be the ending of a quote
#                # pair
#                elif quote_stack[-1] == c:
#                    quote_stack.pop()
#            
#            # if an open curly bracket was found...
#            if c == "{":
#                print("{ - bc=%d qs=%d" % (current_json_bracket_count, len(quote_stack)))
#                # if we aren't already recording a JSON object, start a new one
#                if current_json_start < 0:
#                    current_json_start = i
#                    current_json_quote_nest = len(quote_stack)
#                    current_json_bracket_count = 1
#                # otherwise, if one has already been started, simply increase
#                # the bracket count if we're inside the same nesting level as
#                # the original start of the JSON
#                elif len(quote_stack) == current_json_quote_nest:
#                    current_json_bracket_count += 1
#            # if a closing curly bracket was found...
#            elif c == "}":
#                print("} - bc=%d qs=%d" % (current_json_bracket_count, len(quote_stack)))
#                # if we're recording a JSON object, decrement so long as we're
#                # on the same quote nesting level as we originally started on
#                if current_json_start >= 0:
#                    if len(quote_stack) == current_json_quote_nest:
#                        current_json_bracket_count -= 1
#
#                    # if we've reached the end of the JSON object, save it
#                    if current_json_bracket_count == 0:
#                        print("START=%d (%s), END=%d (%s)" % (current_json_start, type(current_json_start), i, type(i)))
#                        jstr = txt[current_json_start:i]
#                        results.append(jstr)
#                        # reset the JSON info
#                        current_json_start = -1
#                        current_json_quote_nest = -1
#            
#            # increment to the next character
#            i += 1
#
#        return results
    

# ============================== Kayak Scraper =============================== #
class FlightScraper_Kayak(FlightScraper):
    # Constructor.
    def __init__(self, config):
        super().__init__(config)
        self.url = "https://www.kayak.com/flights"
   
    # Creates and returns one or more Kayak URLs given the parameters and the
    # desired embark and return dates.
    # Example:
    #   https://www.kayak.com/flights/SEA-DEN/2023-10-26/2023-11-02/1adults
    #   https://www.kayak.com/flights/SEA-DEN/2023-10-26/1adults
    def make_urls(self, params: TripFlightConfig,
                  dt_embark: datetime,
                  dt_return: datetime):
        # Helper function to construct a URL
        def make_url_helper(ap1, ap2, dt1, dt2, num_adults):
            url = "%s/%s-%s/" % (self.url, ap1, ap2)
            url += "%s/" % dt1.strftime("%Y-%m-%d")
            if dt2 is not None:
                url += "%s/" % dt2.strftime("%Y-%m-%d")
            url += "%dadults" % num_adults
            return url

        urls = {}
        # depending on the airports, we want either a single URL (for a two-way
        # ticket deal) or two URLs (one for the embark, one for the return)
        # TODO - implement children support at some point
        if params.airport_embark_depart == params.airport_return_arrive and \
           params.airport_embark_arrive == params.airport_return_depart:
            urls["is_two_way"] = True
            urls["url_embark"] = make_url_helper(params.airport_embark_depart,
                                                 params.airport_embark_arrive,
                                                 dt_embark,
                                                 dt_return,
                                                 params.passengers_adult)
        else:
            urls["is_two_way"] = False
            urls["url_embark"] = make_url_helper(params.airport_embark_depart,
                                                 params.airport_embark_arrive,
                                                 dt_embark,
                                                 None,
                                                 params.passengers_adult)
            urls["url_return"] = make_url_helper(params.airport_return_depart,
                                                 params.airport_return_arrive,
                                                 dt_return,
                                                 None,
                                                 params.passengers_adult)

        return urls
 
    def scrape(self, params: TripFlightConfig,
               dt_embark: datetime,
               dt_return: datetime,
               count=10):
        
        assert self.browser is not None, "You must first call browser_open()"
        b = self.browser

        # send the browser to the correct URL, given the dates and trip params
        urls = self.make_urls(params, dt_embark, dt_return)
        print("Browser: %s" % b)
        print("Sending browser to: %s" % urls["url_embark"])
        b.get(urls["url_embark"])
        
        # parse out ticket prices from the HTML and use their locations to
        # search for other flight information in the surrounding HTML
        src = b.page_source
        for match in re.finditer(">\$\d+<", src):
            # retrieve the starting and ending indexes, and use them to grab the
            # matched string
            idx_start = match.start()
            idx_end = match.end()
            mstr = src[idx_start:idx_end]
            
            # use the location of neighboring matches (if any) as a measuring
            # stick for how far forward and backward to capture roughly enough
            # HTML to parse out other information about the flight
            print("Match: start=%d, end=%d, %s" % (idx_start, idx_end, mstr))
            #idx_start = match.start()

        # search for JSON strings and extract them from the source HTML
        #jdicts = self.extract_json(src)
        #print("DICTIONARIES:\n%s" % jdicts)

