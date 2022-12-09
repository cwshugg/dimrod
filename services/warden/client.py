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

    # Computes and returns the number of seconds since the client was last seen.
    def time_since_last_seen(self):
        return datetime.now().timestamp() - self.last_seen.timestamp()

    # ------------------------ Dictionary Conversion ------------------------- #
    # Converts the Client object into a JSON dictionary and returns it.
    def to_json(self):
        result = {
            "macaddr": self.macaddr,
            "ipaddr": self.ipaddr,
            "last_seen": self.last_seen.timestamp()
        }
        return result

