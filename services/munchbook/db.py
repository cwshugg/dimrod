# This module defines an interface into the SQLite3 database used by the
# munchbook service to record food entries for a user.

# Imports
import os
import sys
import threading
import calendar
from datetime import datetime
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from entry import MunchbookEntry


class MunchbookDatabase:
    """Manages a SQLite3 database of food entries for a single user."""
    def __init__(self, db_path: str):
        """Constructor."""
        self.db_path = db_path
        self.lock = threading.Lock()
        self.table_name = "entries"

    # ------------------------- Database Interfacing ------------------------- #
    def db_acquire(self):
        """Initializes the database's tables and opens a connection to the
        database.

        The connection is returned. If an error occurs after acquiring the
        lock, the lock is released before re-raising the exception.
        """
        self.lock.acquire()

        try:
            # make sure the database's table exists
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS %s ("
                        "entry_id TEXT PRIMARY KEY, "
                        "description TEXT, "
                        "notes TEXT, "
                        "timestamp INTEGER)" % self.table_name)
            con.commit()

            return con
        except Exception:
            self.lock.release()
            raise

    def db_release(self, con, commit=False):
        """Closes access to the database and, if 'commit' is specified, any
        changes made are committed to disk.
        """
        if commit:
            con.commit()
        con.close()
        self.lock.release()

    # ------------------------------ Interface ------------------------------- #
    def add(self, entry: MunchbookEntry):
        """Adds an entry to the database."""
        con = self.db_acquire()
        try:
            cur = con.cursor()
            cur.execute("INSERT OR REPLACE INTO %s VALUES (?, ?, ?, ?)" %
                        self.table_name, entry.to_sqlite3())
            self.db_release(con, commit=True)
        except Exception:
            self.db_release(con)
            raise

    def search(self, condition=None, count=None, sort_least_recent=False):
        """Generic search function that requires input of a condition string
        (a SQLite3 WHERE statement).

        WARNING: The 'condition' parameter is inserted directly into the SQL
        query string. This is safe for internal use only — never pass
        user-supplied input as the condition without sanitization.

        If 'sort_least_recent' is specified, sorting will be done such that
        the least-recent entries will appear first (the default is to show the
        most recent entries first).
        If 'count' is specified, at most 'count' entries will be returned.
        """
        # build a command matching the given condition
        cmd = "SELECT * FROM %s" % self.table_name
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # add in the ORDER BY command based on the sort order
        cmd += " ORDER BY timestamp %s" % \
               ("ASC" if sort_least_recent else "DESC")

        # add in the maximum count, if given
        if count is not None:
            if count <= 0:
                return []
            cmd += " LIMIT %d" % count

        # connect, query, and release
        con = self.db_acquire()
        try:
            cur = con.cursor()
            result = cur.execute(cmd)

            # convert all results to a list of entry objects
            entries = []
            for row in result:
                entries.append(MunchbookEntry.from_sqlite3(row))

            # close database and return
            self.db_release(con)
            return entries
        except Exception:
            self.db_release(con)
            raise

    def search_by_time_range(self, start_ts: datetime, end_ts: datetime,
                             count=None):
        """Searches for entries within the given time range.

        The provided datetimes should represent UTC times. Timestamps are
        converted via calendar.timegm() to match the UTC storage format.

        Returns a list of MunchbookEntry objects, ordered most recent first.
        If 'count' is specified, at most 'count' entries will be returned.
        """
        cond = "timestamp >= %d AND timestamp <= %d" % \
               (calendar.timegm(start_ts.timetuple()),
                calendar.timegm(end_ts.timetuple()))
        return self.search(condition=cond, count=count)

    def search_by_time_range_ts(self, start_ts: int, end_ts: int,
                                count=None):
        """Searches for entries within the given time range using raw UTC
        timestamps (integers).

        Returns a list of MunchbookEntry objects, ordered most recent first.
        If 'count' is specified, at most 'count' entries will be returned.
        """
        cond = "timestamp >= %d AND timestamp <= %d" % (start_ts, end_ts)
        return self.search(condition=cond, count=count)
