# Module that defines a configuration class for a service.
#
#   Connor Shugg

# Imports
import os
import sys
import json
from datetime import datetime


# ============================== Config Fields =============================== #
# Represents a single config file field with various properties that are
# enforced when parsed. These fields don't story any live values loaded in from
# real config files, but instead are used simply to make parsing easier.
class ConfigField:
    # Constructor. Takes in:
    #   name        The name of the config file entry.
    #   types       An array of types representing the field's allowed type(s).
    #   required    An optional boolean that indicates if the field is mandatory
    #   default     A default field, useful if required=False
    def __init__(self, name, types, required=False, default=None):
        self.name = name
        self.types = types
        self.required = required
        self.default = default

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
    
    # Creates and returns a string representation of the config object.
    def __str__(self):
        jdata = self.to_json()
        return json.dumps(jdata, indent=4, default=str)
    
    # Returns the `ConfigField` object representing one of the object's fields,
    # or `None` if the field doesn't exist.
    def get_field(self, name: str):
        for f in self.fields:
            if name == f.name:
                return f
        return None
            
    # Takes in a file path, opens it for reading, and attempts to parse all
    # fields defined in the class' 'fields' property.
    def parse_file(self, fpath: str):
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
    def parse_json(self, jdata: dict):
        # iterate through each field we expect to see
        for f in self.fields:
            key = f.name
            required = f.required
            types = f.types

            # if it exists, check the type
            if key in jdata:
                # if the expected type is actually a sub-class of this Config
                # class, then we have a nested config object
                val = None
                if issubclass(types[0], Config):
                    cls = types[0]
                    # if the actual value is a dictionary, parse it as the
                    # sub-config class
                    if type(jdata[key]) == dict:
                        c = cls()
                        c.parse_json(jdata[key])
                        val = c
                    # otherwise, if it's a list of objects, parse each one as an
                    # instance of the sub-config class
                    elif type(jdata[key]) == list:
                        items = []
                        for entry in jdata[key]:
                            c = cls()
                            c.parse_json(entry)
                            items.append(c)
                        val = items
                
                # if the expected type is a datetime, assume that the value
                # provided is a string in the ISO date format
                if types[0] == datetime:
                    msg = "%s entry \"%s\" must be a datetime value represented as an ISO string" % \
                          (self.fpath if self.fpath else "json", key)
                    self.check(type(jdata[key]) == str, msg)
                    
                    # parse as an ISO string
                    val = datetime.fromisoformat(jdata[key])

                # if the above attempts didn't work, assume we're dealing with
                # a simple type of object
                if val is None:
                    val = jdata[key]
                    
                    # if the value isn't required, `None` is allowed
                    if required:
                        msg = "%s entry \"%s\" is required: it cannot be None" % \
                              (self.fpath if self.fpath else "json", key)
                        self.check(val is not None, msg)

                    # ensure the value is of the correct type, if it's not None
                    if val is not None:
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
                # otherwise, set the value to the field's default
                setattr(self, key, f.default)

        # if there are any other fields in the config that aren't specified
        # explicitly, we'll save them to a separate dictionary
        field_names = []
        for f in self.fields:
            field_names.append(f.name)
        self.extra_fields = {}
        for key in jdata:
            if key not in field_names:
                self.extra_fields[key] = jdata[key]
                setattr(self, key, jdata[key])
    
    # A custom assertion function for the config class.
    def check(self, condition, msg):
        if not condition:
            raise Exception("Config Error: %s" % msg)

    # Converts the config into a JSON dictionary and returns it.
    def to_json(self):
        # Converts a given object to a JSON-friendly format
        def obj_to_json(obj):
            # if it's a subclass of our Config parent class, recursively invoke
            # its `to_json()` function
            if issubclass(obj.__class__, Config):
                return obj.to_json()
            
            # if the object is a datetime, convert it to an ISO string
            if type(obj) == datetime:
                return obj.isoformat()

            return obj

        # Helper function for converting config objects back to JSON.
        def to_json_helper(obj):
            # if the object is a list, convert each entry individually
            if type(obj) == list:
                result = []
                for entry in obj:
                    result.append(obj_to_json(entry))
                return result
            # otherwise, just convert the object itself
            return obj_to_json(obj)

        result = {}
        # convert all expected fields to JSON
        for f in self.fields:
            result[f.name] = to_json_helper(getattr(self, f.name))
        # convert all extra fields to JSON
        for e in self.extra_fields:
            result[e] = to_json_helper(getattr(self, e))

        return result
    
    # Converts the object to JSON, then encodes it into a `bytes` object.
    def to_bytes(self):
        return json.dumps(self.to_json()).encode("utf-8")
    
    # Takes in a `bytes` object and uses it to decode and parse the JSON string
    # within.
    def parse_bytes(self, b: bytes):
        return self.parse_json(json.loads(b.decode("utf-8")))
    
    # Converts the object to an encoded hex string.
    def to_hex(self):
        return self.to_bytes().hex()

    # Takes in a hex string and uses it to decode and parse the JSON contents.
    def parse_hex(self, hstr: str):
        return self.parse_bytes(bytes.fromhex(hstr))
    
    # Converts the object into a tuple for use in a SQLite3 database. By
    # default, the object is converted to a JSON string, then encoded and
    # represented as a single hex string (a tuple of length 1).
    #
    #   ("(encoded JSON string)")
    #
    # However, the caller can specify `fields_to_keep_visible` as a list of
    # field names that belong to this object. All fields within this list will
    # *not* be encoded, and instead will be placed directly as tuple entries.
    #
    # For example: if your object has a field called `obj_id`, and you want
    # that to be present in the tuple and *not* encoded, you can specify
    # `fields_to_keep_visible=["obj_id"]`, and your tuple will look like this:
    #
    #   ("(encoded JSON string)", "VALUE_OF_OBJ_ID_FIELD")
    #
    # All fields specified in `fields_to_keep_visible` must be simple,
    # primitive types that can be represented by SQLite3 (such as integers or
    # strings).
    def to_sqlite3(self, fields_to_keep_visible=[]):
        # encode the entire object as a hex string and store it as the first
        # field in the tuple
        result = (self.to_hex(),)

        # any `fields_to_keep_visible` are added as additional tuple entries
        for name in fields_to_keep_visible:
            assert hasattr(self, name), \
                   "a field that doesn't belong to %s was specified" % \
                   __class__.__name__
            val = getattr(self, name)
            
            # make sure the value is a primitive type
            assert type(val) in [int, float, str, bool], \
                   "only primitive types can be placed into a SQLite3 tuple for an %s object" % \
                   __class__.__name__
            
            # convert booleans to integers
            if type(val) == bool:
                val = int(val)
            result += (val,)

        return result
    
    # Creates a SQLite `CREATE TABLE` statement used to store this type of
    # object, bearing in mind the same `fields_to_keep_visible` as described
    # above in `to_sqlite3()`.
    #
    # Additionally, a `primary_key_field` can be specified as the name of a
    # field to represent the entry's primary ID. If this is specified, the
    # given field name must also be present in `fields_to_keep_visible`.
    #
    # The SQLite statement is returned.
    def get_sqlite3_table_definition(self, table_name: str,
                                     fields_to_keep_visible=[],
                                     primary_key_field=None):
        result = "CREATE TABLE IF NOT EXISTS %s (" % table_name
        result += "encoded_obj TEXT, "

        # if a primary key field was given, make sure it is part of
        # `fields_to_keep_visible`
        if primary_key_field is not None:
            assert primary_key_field in fields_to_keep_visible, \
                   "the given primary key field \"%s\" must also be present in `fields_to_keep_visible`"

        # add all visible fields as separate colums
        fields_to_keep_visible_len = len(fields_to_keep_visible)
        for (i, name) in enumerate(fields_to_keep_visible):
            assert hasattr(self, name), \
                   "a field that doesn't belong to %s was specified" % \
                   __class__.__name__
            val = getattr(self, name)

            # determine the correct SQLite3 type to be used for this data type
            # (first, make sure the value is a primitive type)
            assert type(val) in [int, float, str, bool], \
                   "only primitive types can be placed into a SQLite3 tuple for an %s object" % \
                   __class__.__name__
            sqlite3_type = "BLOB"
            if type(val) == str:
                sqlite3_type = "TEXT"
            elif type(val) in [int, bool]:
                sqlite3_type = "INTEGER"
            elif type(val) == float:
                sqlite3_type = "REAL"
            
            # add the name and type definition
            result += "%s %s" % (name, sqlite3_type)

            # add the "PRIMARY KEY" specifier if this field is supposed to be
            # the primary key
            if name == primary_key_field:
                result += " PRIMARY KEY"

            # add a comma and space if this isn't the last field
            if i < fields_to_keep_visible_len - 1:
                result += ", "

        result += ")"
        return result
    
    # Takes in an object and parses the given SQLite3 tuple.
    # If any fields names are specified in the `field_kept_visible` array,
    # their values are retrieved from the extra tuple entries and used to
    # update the decoded object.
    def parse_sqlite3(self, tdata: tuple, fields_kept_visible=[]):
        # the first field must be the encoded object; decode it
        assert len(tdata) >= (1 + len(fields_kept_visible))

        # parse the first field as an encoded hex string
        self.parse_hex(tdata[0])

        # iterate through any fields that were kept visible
        tuple_idx = 1
        for name in fields_kept_visible:
            field = self.get_field(name)
            value = tdata[tuple_idx]

            assert field is not None, \
                   "a field that doesn't belong to %s was specified" % \
                   __class__.__name__

            # convert any expected booleans from integers
            if bool in field.types:
                value = bool(value)

            # otherwise, make sure the value's type is correct
            assert type(value) in field.types, \
                   "value %s has type %s, but type(s) %s was expected for field %s" % \
                   (str(value), type(value), str(field.types), field.name)
            
            # set the object's field to the value and increment
            setattr(self, field.name, value)
            tuple_idx += 1

