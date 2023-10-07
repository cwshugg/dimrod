# This module defines the Event object, which represents a single occurrence of
# something that happened, that is to be recorded in the historian's database.

# Imports
import os
import sys
import hashlib
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Globals
event_tag_delimeter = "/"
event_tag_delimeter_sub = "EVENT_TAG_DELIMETER_SUB"

class HistorianEvent(Config):
    def __init__(self, event_id=None):
        super().__init__()
        self.fields = [
            ConfigField("author",       [str],      required=True),
            ConfigField("title",        [str],      required=True),
            ConfigField("description",  [str],      required=True),
            ConfigField("timestamp",    [int],      required=True),
            ConfigField("tags",         [list],     required=True)
        ]
        self.id = event_id
    
    # Generates and returns a unique ID string for the event.
    def get_id(self):
        # only generate once
        if self.id is not None:
            return self.id
        # generate a SHA hash based on all the event's values, plus a little bit
        # of randomly-generated salt
        idstr = "%s/%s/%s/%s/%s" % (self.author, self.title, self.description,
                                    str(self.timestamp), str(self.tags))
        salt = os.urandom(16)
        return hashlib.sha256(idstr.encode() + salt).hexdigest()
    
    # --------------------------- JSON Conversion ---------------------------- #
    # Overridden JSON parsing function.
    def parse_json(self, jdata: dict):
        super().parse_json(jdata)

        # convert the timestamp from an integer to a datetime object
        self.timestamp = datetime.fromtimestamp(self.timestamp)
    
    # Overridden JSON conversion function.
    def to_json(self, include_id=False):
        # convert the timestamp back into an integer
        self.timestamp = int(self.timestamp.timestamp())

        # call parent conversion function
        result = super().to_json()

        # if asked to include the event's ID, add it to the dictionary
        if include_id:
            result["event_id"] = self.get_id()
        return result

    # -------------------------- SQLite3 Conversion -------------------------- #
    # Helper function to convert a list of string tags into a SQLite3-friendly
    # string.
    @staticmethod
    def tags_to_sqlite3(tags: list):
        tagstr = ""
        for (i, tag) in enumerate(tags):
            t = tag.replace(event_tag_delimeter, event_tag_delimeter_sub)
            tagstr += "%s%s" % (t, event_tag_delimeter if i < len(tags) - 1 else "")
        return tagstr
    
    # Converts the object to a tuple, useable by SQLite3 for inserting into a
    # database.
    def to_sqlite3(self):
        # there isn't a clean way to store a list of strings (the event's tags)
        # into the database, so we'll convert it to a concatenated string
        tagstr = self.tags_to_sqlite3(self.tags)
        return (self.get_id(), self.author, self.title, self.description,
                int(self.timestamp.timestamp()), tagstr)
    
    # Takes in a tuple and attempts to convert it to an event object.
    @staticmethod
    def from_sqlite3(tdata: tuple):
        assert len(tdata) >= 6, "Tuple does not contain enough fields."
        eid = str(tdata[0])

        # convert the concatenated tag string back into a list of strings
        tags = []
        for tag in tdata[5].split(event_tag_delimeter):
            t = tag.replace(event_tag_delimeter_sub, event_tag_delimeter)
            tags.append(t)

        # build a JSON payload and send it to a new HistorianEvent object for
        # parsing
        jdata = {
            "author": str(tdata[1]),
            "title": str(tdata[2]),
            "description": str(tdata[3]),
            "timestamp": int(tdata[4]),
            "tags": tags
        }
        e = HistorianEvent(event_id=eid)
        e.parse_json(jdata)
        return e

