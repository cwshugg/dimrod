# This module defines an interface into the SQLite3 database used by the
# historian to record events.

# Imports
import os
import sys
import threading
from datetime import datetime
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from event import HistorianEvent

class HistorianDatabase:
    # Constructor.
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.Lock()

    # ------------------------- Database Interfacing ------------------------- #
    # Initializes the database's tables and opens a connection to the database.
    # The connection is returned.
    def db_acquire(self):
        self.lock.acquire()
        con = sqlite3.connect(self.db_path)

        # make sure the database's table exists
        # TODO

        return con
    
    # Closes access to the database and, if 'commit' is specified, any changes
    # made are committed to disk.
    def db_release(self, con, commit=False):
        if commit:
            con.commit()
        con.close()
        self.lock.release()

    # ------------------------------ Interface ------------------------------- #
    # Adds an event to the database.
    def add(self, event: HistorianEvent):
        # TODO
        pass
    
    # Searches for an event with the given ID. Returns it if one is found.
    # Otherwise, returns None.
    def search_by_id(self, eid: str):
        # TODO
        return None
    
    # Searches for the most recent events occurring between the two given
    # datetimes (or before/after the only specified one, if only one is
    # specified.
    # If 'count' is specified, only 'count' events will be returned.
    def search_by_timestamp(self, ts_before=None, ts_after=None, count=None):
        # TODO
        return []
    
    # Searches for the latest events matching the given author name.
    # Returns a list of events, or an empty list.
    # If 'count' is specified, only the latest 'count' events will be returned.
    def search_by_author(self, author: str, count=None):
        # TODO
        return []

    # Works the same as 'search_by_author', but searches for a matching title.
    def search_by_title(self, title: str, count=None):
        # TODO
        return []

    # Works the same as 'search_by_author', but searches for events that contain
    # the specified tags.
    def search_by_tags(self, tags: list, count=None):
        # TODO
        return []


