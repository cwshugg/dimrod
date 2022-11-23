# This module defines a single network-connected client.

# Imports
from datetime import datetime

class Client:
    # Constructor.
    def __init__(self, macaddr: str):
        self.macaddr = macaddr.lower()
        self.ipaddr = None
        self.last_seen = datetime.fromtimestamp(0)

    # Creates and returns a string representation of the client.
    def __str__(self):
        return "%s: %s" % (self.macaddr, self.ipaddr)
    
    # Takes in an IP address and updates it internally.
    def update(self, ipaddr=None):
        # only update the IP address if one was given
        if ipaddr:
            self.ipaddr = ipaddr
        # update the last-seen time regardless
        self.last_seen = datetime.now()

