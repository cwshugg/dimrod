# This module defines the recipe object (and other similar objects), which are
# used to represent recipes and ingredients for cooking and baking.

# Imports
import os
import sys
import enum

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField

# An enum representing the rough frequency at which the supply of an ingredient
# should be replenished.
class IngredientReplenishType(enum.Enum):
    ALWAYS = 0
    SOMETIMES = 1
    RARELY = 2

# Represents a single ingredient in a recipe.
class Ingredient(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("id",                    [str],      required=True),
            UniserdesField("title",                 [str],      required=False, default=None),
            UniserdesField("description",           [str],      required=False, default=None),
            UniserdesField("quantity",              [float],    required=False, default=1.0),
            UniserdesField("replenish", [IngredientReplenishType], required=False, default=IngredientReplenishType.ALWAYS),
            UniserdesField("is_optional",           [bool],     required=False, default=False),
        ]

    def post_parse_init(self):
        # Strip whitespace and force the ID string to be lowercase:
        self.id = self.id.strip().lower()

# Represents a single step to follow in a recipe.
class RecipeStep(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("id",            [str],      required=True),
            UniserdesField("title",         [str],      required=False, default=None),
            UniserdesField("description",   [str],      required=False, default=None),
        ]

# Represents a combination of ingredients that form a recipe.
class Recipe(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("id",            [str],          required=True),
            UniserdesField("ingredients",   [Ingredient],   required=True),
            UniserdesField("steps",         [RecipeStep],   required=True),
            UniserdesField("title",         [str],          required=False, default=None),
            UniserdesField("description",   [str],          required=False, default=None),
            UniserdesField("links",         [list],         required=False, default=None),
            UniserdesField("servings",      [int],          required=False, default=1),
            UniserdesField("icon",          [str],          required=False, default=None),
        ]

    def post_parse_init(self):
        # Strip whitespace and force the ID string to be lowercase:
        self.id = self.id.strip().lower()

