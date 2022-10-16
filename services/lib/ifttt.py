# A module that utilizes the 'requests' library to communicate with IFTTT
# webhooks I've set up on my account.

# Imports
import os
import sys
import requests

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config


# ============================== Webhook Config ============================== #
class WebhookConfig(lib.config.Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("webhook_key",   [str],      required=True)
        ]


# =========================== Webhook Pinger Class =========================== #
# This class defines a mechanism to send IFTTT webhook requests.
class Webhook:
    # Constructor. Takes in a config file path.
    def __init__(self, config_path):
        self.config = WebhookConfig()
        self.config.parse_file(config_path)
    
    # Takes in two parameters and sends a webhook:
    #   1. Webhook event name (string)
    #   2. Webhook JSON data (dict) (optional)
    # The 'Response' object is returned.
    def send(self, event, jdata):
        # set up the request URL
        url = "https://maker.ifttt.com/trigger/%s/json/with/key/%s" % \
              (event, self.config.webhook_key)

        # set up a session and establish headers
        s = requests.Session()
        s.headers["Content-Type"] = "application/json"

        # send the request
        resp = s.post(url, json=jdata)
        return resp

    # ---------------------------- Public Helpers ---------------------------- #
    # Takes in the response from 'send' and returns the status code.
    def get_status_code(self, response):
        return response.status_code
    
    # Looks for and returns the error message from IFTTT's response JSON.
    # Returns a list of string messages, or en empty list.
    def get_errors(self, response):
        result = []
        try:
            jdata = response.json()
            for error in jdata["errors"]:
                result.append(error["message"])
        except:
            pass
        return result


