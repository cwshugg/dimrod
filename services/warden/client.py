# This module defines a single network-connected client.

# Imports
from datetime import datetime


# ================================== Client ================================== #
class Client:
    # Constructor.
    def __init__(self, macaddr: str, log_maxlen=100):
        self.macaddr = macaddr.lower()
        self.ipaddr = None
        self.log = ClientLog(log_maxlen)

    # Creates and returns a string representation of the client.
    def __str__(self):
        return "%s: %s" % (self.macaddr, self.ipaddr)

    # Takes in an IP address and updates it internally.
    def update(self, ipaddr=None):
        # only update the IP address if one was given
        if ipaddr:
            self.ipaddr = ipaddr
        # update the last-seen time regardless
        self.log.push(datetime.now())

    # Computes and returns the number of seconds since the client was last seen.
    def time_since_last_seen(self):
        if len(self.log) == 0:
            return 999999999999
        return datetime.now().timestamp() - self.log.get_latest().timestamp()

    # ------------------------ Dictionary Conversion ------------------------- #
    # Converts the Client object into a JSON dictionary and returns it.
    def to_json(self):
        # determine when "last seen" was, depending on what entries are in the
        # client's ping log
        last_seen = datetime.fromtimestamp(0)
        if len(self.log) > 0:
            last_seen = self.log.get_latest()

        result = {
            "macaddr": self.macaddr,
            "ipaddr": self.ipaddr,
            "last_seen": last_seen.timestamp(),
        }
        return result


# ============================= Client Ping Log ============================== #
# This class represents a queue-like data structure that stores the last N
# ping results for the client.
class ClientLog:
    # Constructor.
    def __init__(self, maxlen):
        self.maxlen = maxlen
        self.queue = []

    # Iterates through the log's internal queue.
    def __iter__(self):
        for entry in self.queue:
            yield entry

    # Returns the length of the inner queue.
    def __len__(self):
        return len(self.queue)

    # Returns the latest entry in the queue, or None if the queue is empty.
    def get_latest(self):
        return self.queue[0] if len(self.queue) > 0 else None

    # Returns the earliest entry in the queue, or None if the queue is empty.
    def get_earliest(self):
        return self.queue[-1] if len(self.queue) > 0 else None

    # Pushes a new entry onto the client log.
    def push(self, dt):
        # pop the last entry from the queue, if necessary
        if len(self.queue) == self.maxlen:
            self.queue.pop(-1)
        self.queue.insert(0, dt)

