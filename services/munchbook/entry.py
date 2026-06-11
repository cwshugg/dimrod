# This module defines the MunchbookEntry object, which represents a single
# food entry to be recorded in a user's munchbook database.

# Imports
import calendar
import os
import sys
import hashlib
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField


class MunchbookEntry(Uniserdes):
    """Represents a single food entry in the munchbook."""
    def __init__(self, entry_id=None):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("timestamp",    [int],      required=True),
            UniserdesField("description",  [str],      required=True),
            UniserdesField("notes",        [str],      required=False,  default=""),
            UniserdesField("ingredients",  [list],     required=False,  default=[]),
        ]
        self.id = entry_id

    def get_id(self):
        """Generates and returns a unique ID string for the entry."""
        # only generate once
        if self.id is not None:
            return self.id
        # generate a SHA hash based on all the entry's values, plus a little
        # bit of randomly-generated salt
        idstr = "%s/%s/%s/%d" % (
            self.description,
            self.notes,
            ",".join(self.ingredients) if self.ingredients else "",
            calendar.timegm(self.timestamp.timetuple()),
        )
        salt = os.urandom(16)
        self.id = hashlib.sha256(idstr.encode() + salt).hexdigest()
        return self.id

    # --------------------------- JSON Conversion ---------------------------- #
    def parse_json(self, jdata: dict, base_path: str = None):
        """Overridden JSON parsing function."""
        super().parse_json(jdata, base_path=base_path)

        # convert the timestamp from an integer to a datetime object
        self.timestamp = datetime.utcfromtimestamp(self.timestamp)

    def to_json(self, include_id=False):
        """Overridden JSON conversion function."""
        # call parent conversion function
        result = super().to_json()
        result["timestamp"] = calendar.timegm(self.timestamp.timetuple())

        # if asked to include the entry's ID, add it to the dictionary
        if include_id:
            result["entry_id"] = self.get_id()
        return result

    # -------------------------- SQLite3 Conversion -------------------------- #
    def to_sqlite3(self):
        """Converts the object to a tuple, useable by SQLite3 for inserting
        into a database.
        """
        timestamp = calendar.timegm(self.timestamp.timetuple())
        ingredients_str = ",".join(self.ingredients) \
            if self.ingredients else ""
        return (self.get_id(), self.description, self.notes,
                timestamp, ingredients_str)

    @staticmethod
    def from_sqlite3(tdata: tuple):
        """Takes in a tuple and attempts to convert it to a MunchbookEntry
        object.
        """
        assert len(tdata) >= 5, "Tuple does not contain enough fields."
        eid = str(tdata[0])

        # parse comma-separated ingredients string back to a list
        ingredients_str = str(tdata[4]) if len(tdata) > 4 else ""
        ingredients = [i.strip() for i in ingredients_str.split(",")
                       if i.strip()] if ingredients_str else []

        # build a JSON payload and send it to a new MunchbookEntry object
        # for parsing
        jdata = {
            "description": str(tdata[1]),
            "notes": str(tdata[2]),
            "timestamp": int(tdata[3]),
            "ingredients": ingredients,
        }
        e = MunchbookEntry(entry_id=eid)
        e.parse_json(jdata)
        return e
