# Module that defines a configuration class for a service.
#
#   Connor Shugg

# Imports
import os
import sys
import json


# ============================== Config Fields =============================== #
# Represents a single config file field with various properties that are
# enforced when parsed. These fields don't story any live values loaded in from
# real config files, but instead are used simply to make parsing easier.
class ConfigField:
    # Constructor. Takes in:
    #   name        The name of the config file entry.
    #   types       An array of types representing the field's allowed type(s).
    #   required    An optional boolean that indicates if the field is mandatory
    def __init__(self, name, types, required=False):
        self.name = name
        self.types = types
        self.required = required

    # Checks the type of a given value against the field's type. Returns True if
    # the type matches one of the field's types.
    def type_match(self, value):
        return type(value) in self.types


# ============================ Main Config Class ============================= #
# Config class.
class Config:
    # Constructor. Takes in the JSON file path and reads it in.
    def __init__(self):
        self.fields = []
        self.fpath = None
            
    # Takes in a file path, opens it for reading, and attempts to parse all
    # fields defined in the class' 'fields' property.
    def parse_file(self, fpath):
        # slurp the entire file contents (not ideal, but the config files
        # shouldn't be too big)
        self.fpath = fpath
        fp = open(fpath)
        content = fp.read()
        fp.close()

        # convert to JSON and define a number of expected fields, then invoke
        # the JSON parsing function
        jdata = json.loads(content)
        self.parse_json(jdata)

    # Used to parse config entries from a dictionary.
    def parse_json(self, jdata):
        # iterate through each field we expect to see
        for f in self.fields:
            key = f.name
            required = f.required
            types = f.types

            # if it exists, check the type
            if key in jdata:
                # ensure the value is of the correct type
                val = jdata[key]
                msg = "%s entry \"%s\" must be of type: %s" % \
                      (self.fpath if self.fpath else "json", key, types)
                self.check(f.type_match(val), msg)
                
                # set the class's attribute and move onto the next field
                setattr(self, key, val)
                continue

            # if it doesn't exist, and it's required, force an assertion failure
            # so the user knows it needs to be included
            if required:
                msg = "%s must contain \"%s\"" % \
                      (self.fpath if self.fpath else "json", key)
                self.check(key in jdata, msg)
            else:
                # otherwise, null-out the field
                setattr(self, key, None)
    
    # A custom assertion function for the config class.
    def check(self, condition, msg):
        if not condition:
            raise Exception("Config Error: %s" % msg)

