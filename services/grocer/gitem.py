# This module defines a class representing a single grocery list item.

# Imports
import hashlib


# =============================== List Config ================================ #
# Class that represents the required fields for a single grocery item object.
class GrocerItemConfig(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=True),
            ConfigField("name",             [str],      required=True),
            ConfigField("description",      [str],      required=True),
            ConfigField("quantity",         [float],    required=True)
        ]


# =================================== List =================================== #
class GrocerItem:
    # Constructor.
    def __init__(self, name: str, description: str, iid=None, quantity=1.0):
        # if no ID is given, generate a new one
        if not iid:
            text = self.name + self.description + "_" + str(id(self))
            h = hashlib.sha256()
            h.update(text.encode("utf-8"))
            iid = h.hexdigest()

        # create a new GrocerItemConfig and store it internally
        values = {
            "id": iid,
            "name": name,
            "description": description,
            "quantity": quantity
        }
        self.config = GrocerItemConfig()
        self.config.parse_json(values)

    # ------------------------------- Setters -------------------------------- #
    # Sets the name.
    def set_name(self, name: str):
        self.config.name = name
    
    # Sets the description.
    def set_description(self, description: str):
        self.config.description = description
    
    # Sets the quantity.
    def set_quantity(self, quantity: float):
        self.config.quantity = quantity

    # --------------------------------- JSON --------------------------------- #
    # Creates and returns a JSON representation of the object.
    def to_json(self):
        return self.config.to_json()

