# This module defines a grocery list class. It represents a single list of items
# to be purchased at the grocery store.

# Imports
import hashlib


# =============================== List Config ================================ #
# Class that represents the required fields for a single grocery list object.
class GrocerListConfig(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=True),
            ConfigField("name",             [str],      required=True),
            ConfigField("description",      [str],      required=True),
            ConfigField("items",            [list],     required=True)
        ]


# =================================== List =================================== #
class GrocerList:
    # Constructor.
    def __init__(self, name: str, description: str, glid=None):
        # if no ID is given, generate a new one
        if not glid:
            text = self.name + self.description + "_" + str(id(self))
            h = hashlib.sha256()
            h.update(text.encode("utf-8"))
            glid = h.hexdigest()

        # create an internal config object
        values = {
            "id": glid,
            "name": name,
            "description": description,
            "items": [],
        }
        self.config = GrocerListConfig()
        self.config.parse_json(values)
    
    # Adds a new grocery item to the list.
    def add(self, item: GrocerItem):
        self.config.items.append(item)
    
    # Removes a specific item from the list.
    def remove(self, item: GrocerItem):
        # TODO
        pass

    # --------------------------------- JSON --------------------------------- #
    # Creates and returns a JSON representation of the grocery list.
    def to_json(self):
        result = self.config.to_json()
        return result
    
