# This module defines a simple List object, used by Scribo.

# Imports
import os
import sys
import json
import flask
import hashlib
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ============================= List Item Class ============================== #
class ScriboListItem:
    # Constructor.
    def __init__(self, text: str, iid=None):
        self.iid = iid
        self.text = text
        self.get_id()

    # String representation.
    def __str__(self):
        return "%s: %s" % (self.get_id(), self.text)
    
    # Returns the list item's unique ID string.
    def get_id(self):
        # if the IID isn't defined, generate one on-demand
        if self.iid is None:
            h = hashlib.sha256()
            h.update(self.text.encode("utf-8"))
            h.update(os.urandom(16))
            self.iid = h.hexdigest()
        return self.iid

    # -------------------------- SQLite3 Conversion -------------------------- #
    # Returns a string representation of the object, as a tuple, to be used in a
    # SQLite3 command.
    def to_sqlite3_str(self):
        return str(self.to_tuple())
    
    # Converts the object's fields into a tuple readable by SQLite3.
    def to_tuple(self):
        return (self.get_id(), self.text)
    
    # Converts a given tuple into a ScriboListItem object.
    @staticmethod
    def from_tuple(t: tuple):
        assert len(t) >= 2, "not enough fields in the given tuple"
        assert type(t[0]) == str, "first tuple field must be a string"
        assert type(t[1]) == str, "second tuple field must be a string"
        return ScriboListItem(t[1], iid=t[0])

    # --------------------------- JSON Conversion ---------------------------- #
    # Converts the object to JSON and returns it.
    def to_json(self):
        return {"iid": self.get_id(), "text": self.text}


# ================================ List Class ================================ #
class ScriboList:
    # Constructor.
    def __init__(self, path: str):
        self.path = path
        self.db_init()

    # Returns all list items in the list.
    def get_all(self):
        return self.db_command("SELECT * FROM items", fetch=True)
    
    # Looks for an entry given the ID. Returns a single item, or None.
    def search_by_id(self, iid: str):
        result = self.db_command("SELECT * FROM items WHERE iid='%s'" % iid, fetch=True)
        return None if len(result) == 0 else result[0]
    
    # Looks for items that contain the given text. Returns a filled or empty
    # list.
    def search_by_text(self, text: str):
        command = "SELECT * FROM items WHERE " \
                  "description LIKE '%%%s%%' OR " \
                  "description LIKE '%s%%' OR " \
                  "description LIKE '%%%s' OR " \
                  "description LIKE '%s'" % \
                  (text, text, text, text)
        return self.db_command(command, fetch=True)
    
    # Adds a new item to the list. Throws an exception if the item already
    # exists in the list.
    def add(self, item: ScriboListItem):
        self.db_command("INSERT INTO items VALUES %s" % item.to_sqlite3_str(), commit=True)
    
    # Removes the given item from the list. Throws an exception if the item
    # isn't found in the list.
    def remove(self, item: ScriboListItem):
        assert self.search_by_id(item.get_id()) is not None, \
               "an item with ID \"%s\" could not be found" % item.get_id()
        self.db_command("DELETE FROM items WHERE iid='%s'" % item.get_id(), commit=True)

    # -------------------------- SQLite3 Interface --------------------------- #
    # Initializes the database.
    def db_init(self):
        # if the file doesn't exist, create it
        if not os.path.isfile(self.path):
            fp = open(self.path, "w")
            fp.close()
        
        # open a database connection and get a cursor
        connection = sqlite3.connect(self.path)
        c = connection.cursor()

        # create a table, if it doesn't exist, to contain the list's items
        c.execute("CREATE TABLE IF NOT EXISTS items "
                  "(iid TEXT PRIMARY KEY, description TEXT)")

        # commit changes and disconnect
        connection.commit()
    
    # Runs a generic command, performing commit() or fetchall() accordingly.
    def db_command(self, cmd: str, commit=False, fetch=False):
        self.db_init()
        connection = sqlite3.connect(self.path)
        c = connection.cursor()

        # execute the given command then commit
        result = c.execute(cmd)
        if commit:
            connection.commit()
        if fetch:
            items = result.fetchall()
            result = []
            for i in items:
                result.append(ScriboListItem.from_tuple(i))
            return result
        return []

    # --------------------------- JSON Conversion ---------------------------- #
    # Converts the list to a JSON object and returns it.
    def to_json(self):
        result = []
        items = self.get_all()
        for i in items:
            result.append(i.to_json())
        return result

