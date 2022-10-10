# Module that defines a configuration class for a service.
#
#   Connor Shugg

# Imports
import os
import sys
import json

# Config class.
class Config:
    # Constructor. Takes in the JSON file path and reads it in.
    def __init__(self, fpath):
        # slurp the entire file contents (not ideal, but the config files
        # shouldn't be too big)
        fp = open(fpath)
        content = fp.read()
        fp.close()

        # convert to JSON and define a number of expected fields
        jdata = json.loads(content)
        fields = [
            # NAME              REQUIRED?       ALLOWED TYPES
            ["name",            True,           [str]],
            ["server_addr",     True,           [str]],
            ["server_port",     True,           [int]]
        ]

        # iterate through each field we expect to see
        for f in fields:
            key = f[0]
            required = f[1]
            types = f[2]

            # if it exists, check the type
            if key in jdata:
                # ensure the value is of the correct type
                val = jdata[key]
                msg = "the service's config entry \"%s\" must be of type: %s" % (key, types)
                assert type(val) in types, msg
                
                # set the class's attribute and move onto the next field
                setattr(self, key, val)
                continue

            # if it doesn't exist, and it's required, force an assertion failure
            # so the user knows it needs to be included
            if required:
                msg = "the service's config must contain \"%s\"" % key
                assert key in jdata, msg

