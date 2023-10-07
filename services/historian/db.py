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
        self.table_name = "events"

    # ------------------------- Database Interfacing ------------------------- #
    # Initializes the database's tables and opens a connection to the database.
    # The connection is returned.
    def db_acquire(self):
        self.lock.acquire()

        # make sure the database's table exists
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS %s ("
                    "event_id TEXT PRIMARY KEY, "
                    "author TEXT, "
                    "title TEXT, "
                    "description TEXT, "
                    "timestamp INTEGER, "
                    "tags TEXT)" % self.table_name)
        con.commit()

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
        con = self.db_acquire()
        cur = con.cursor()
        cur.execute("INSERT OR REPLACE INTO %s VALUES %s" %
                    (self.table_name, str(event.to_sqlite3())))
        self.db_release(con, commit=True)
    
    # Generic search function that requires input of a condition string (a
    # SQlite3 WHERE statement).
    # If 'sort_least_recent' is specified, sorting will be done such that the
    # least-recent events will appear first (the default is to show the most
    # recent events first).
    # If 'count' is specified, at most 'count' events will be returned.
    def search(self, table=None, condition=None,
               count=None, sort_least_recent=False):
        # choose the default table name, if none was given
        if table is None:
            table = self.table_name

        # build a command matching the given condition and table
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # add in the ORDER BY command based on the sort order
        cmd += " ORDER BY timestamp %s" % ("ASC" if sort_least_recent else "DESC")

        # add in the maximum count, if given
        if count is not None:
            assert count > 0, "The maximum SQLite3 'count' must be > 0."
            cmd += " LIMIT %d" % count
        
        # connect, query, and release
        con = self.db_acquire()
        cur = con.cursor()
        result = cur.execute(cmd)

        # convert all results to a list of event objects
        events = []
        for entry in result:
            events.append(HistorianEvent.from_sqlite3(entry))
        
        # close database and return
        self.db_release(con)
        return events
    
    # Searches for an event with the given ID. Returns it if one is found.
    # Otherwise, returns None.
    def search_by_id(self, eid: str):
        cond = "event_id == \"%s\"" % eid
        e = self.search(condition=cond)

        # make sure either zero or one entry was found
        e_len = len(e)
        if e_len == 0:
            return None
        assert e_len == 1, "More than one event was found with the same ID: %s" % eid
        return e[0]
    
    # Searches for events occurring most recently before the given timestamp.
    # If 'count' is specified, only 'count' events will be returned.
    def search_by_timestamp_before(self, ts: datetime, count=None):
        cond = "timestamp < %d" % int(ts.timestamp())
        return self.search(condition=cond, count=count)
    
    # Searches for events occurring most recently after the given timestamp.
    # If 'count' is specified, only 'count' events will be returned.
    def search_by_timestamp_after(self, ts: datetime, count=None):
        cond = "timestamp > %d" % int(ts.timestamp())
        return self.search(condition=cond, count=count, sort_least_recent=True)
    
    # Searches for the latest events matching the given author name.
    # Returns a list of events, or an empty list.
    # If 'count' is specified, only the latest 'count' events will be returned.
    def search_by_author(self, author: str, count=None):
        cond = "author == \"%s\"" % author
        return self.search(condition=cond, count=count)

    # Works the same as 'search_by_author', but searches for a matching title.
    def search_by_title(self, title: str, count=None):
        cond = "title == \"%s\"" % title
        return self.search(condition=cond, count=count)

    # Works the same as 'search_by_author', but searches for events that contain
    # the specified tags.
    def search_by_tags(self, tags: list, count=None):
        cond = "tags == \"%s\"" % HistorianEvent.tags_to_sqlite3(tags)
        return self.search(condition=cond, count=count)

