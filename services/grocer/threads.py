# Thread classes for the grocer service.
#
# Each thread runs in a daemon loop, sleeping for a configurable interval
# between iterations. The threads are thin loop wrappers: all per-iteration
# business logic (and the locking around it) lives in the corresponding
# ``GrocerService`` methods (``sort_items``, ``resolve_recipes``,
# ``deduplicate_items``), which each acquire the ``ReadWriteLock`` on the
# parent service (``self.service.todoist_lock``) before mutating Todoist.
#
#   Connor Shugg

# Imports
import os
import sys
import threading
import time
import pickle
import re

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service-level imports. We import Recipe here (and re-export it) so the
# GrocerService recipe-resolution logic can parse chef responses without
# re-deriving the chef import path.
sys.path.insert(0, os.path.join(pdir, "chef"))
from recipe import Recipe, Ingredient, IngredientReplenishType  # noqa: F401

# Symbols this module intentionally re-exports. ``Recipe``, ``Ingredient`` and
# ``IngredientReplenishType`` are imported above purely so ``grocer.py`` can
# import them from here (alongside the thread classes and shared constants)
# without re-deriving the chef import path. Listing them in ``__all__``
# documents the intentional re-export and keeps linters quiet.
__all__ = [
    "AutoSorterThread",
    "RecipeResolverThread",
    "DeduplicatorThread",
    "GrocerySortRecord",
    "Recipe",
    "Ingredient",
    "IngredientReplenishType",
    "RECIPE_MAGIC_STRING",
    "EXPANDED_RECIPE_INGREDIENT_MAGIC",
    "RECIPE_RESOLUTION_FAILURE_MAGIC",
    "AUTOSORT_IGNORE_MAGIC",
    "INGREDIENT_ID_MAGIC",
    "TODOIST_RATE_LIMIT_RETRIES",
    "TODOIST_RATE_LIMIT_TIMEOUT",
    "QUANTITY_RE",
]

# ========================== Magic String Constants ========================== #
RECIPE_MAGIC_STRING = "recipe"
EXPANDED_RECIPE_INGREDIENT_MAGIC = "dimrod::expanded_recipe_ingredient"
RECIPE_RESOLUTION_FAILURE_MAGIC = "dimrod::recipe_resolution_failure"
AUTOSORT_IGNORE_MAGIC = "dimrod::autosort_ignore"
INGREDIENT_ID_MAGIC = "dimrod::ingredient_id::"

# Todoist rate-limit handling constants.
TODOIST_RATE_LIMIT_RETRIES = 10
TODOIST_RATE_LIMIT_TIMEOUT = 30  # seconds

# Regex for parsing quantity from a title like "(2x) Ground Beef" or "(1.5x) Milk".
QUANTITY_RE = re.compile(r'^\((\d+(?:\.\d+)?)x?\)\s*(.+)$')


# ========================== Grocery Sort Record ============================= #
class GrocerySortRecord:
    """Persisted on-disk record that tracks which grocery items have already been
    sorted and into which section they were placed. This prevents re-sorting
    items on every loop iteration.
    """
    def __init__(self):
        self.dict = {}
        self.fpath = os.path.join(
            os.path.realpath(os.path.dirname(__file__)),
            ".grocer_sort_record.pkl",
        )

    def load(self):
        """Loads the record from disk. Returns True on success, False if the
        file doesn't exist yet.
        """
        if not os.path.isfile(self.fpath):
            return False
        with open(self.fpath, "rb") as fp:
            self.dict = pickle.load(fp)
        return True

    def save(self):
        """Persists the record to disk."""
        with open(self.fpath, "wb") as fp:
            pickle.dump(self.dict, fp)

    def get(self, key: str):
        """Returns the value for *key*, or ``None`` if not found."""
        return self.dict.get(key, None)

    def remove(self, key: str) -> bool:
        """Removes *key* from the record. Returns True if the key existed."""
        if key not in self.dict:
            return False
        self.dict.pop(key, None)
        return True

    def set(self, key: str, data):
        """Sets (or overwrites) the value for *key*."""
        self.dict[key] = data


# ========================== Auto-Sorter Thread ============================== #
class AutoSorterThread(threading.Thread):
    """Periodically triggers the auto-sort logic, which uses an LLM to sort
    unsorted grocery items into the appropriate Todoist section (category).

    This thread is a thin loop wrapper; the actual sorting logic lives in
    ``GrocerService.sort_items``.
    """
    def __init__(self, service):
        super().__init__(name="grocer-autosort")
        self.service = service
        self.daemon = True

    def run(self):
        """Thread entry point. Loops forever, sorting items each iteration."""
        self.service.log.write("[autosort] Thread started.")
        while True:
            try:
                self.service.sort_items()
            except Exception as e:
                self.service.log.write("[autosort] Error during update: %s" % str(e))

            time.sleep(self.service.config.autosort_refresh_rate)


# ======================= Recipe-Resolver Thread ============================= #
class RecipeResolverThread(threading.Thread):
    """Periodically triggers recipe resolution, which scans the grocery list
    for items that mention a recipe name, resolves them via the chef service,
    and expands them into individual ingredient tasks.

    This thread is a thin loop wrapper; the actual resolution logic lives in
    ``GrocerService.resolve_recipes``.
    """
    def __init__(self, service):
        super().__init__(name="grocer-recipe-resolver")
        self.service = service
        self.daemon = True

    def run(self):
        """Thread entry point."""
        self.service.log.write("[recipe-resolver] Thread started.")
        while True:
            try:
                self.service.resolve_recipes()
            except Exception as e:
                self.service.log.write("[recipe-resolver] Error during update: %s" % str(e))

            time.sleep(self.service.config.recipe_resolver_refresh_rate)


# ========================== Deduplicator Thread ============================= #
class DeduplicatorThread(threading.Thread):
    """Periodically triggers deduplication, which scans the grocery list for
    items that share the same ``dimrod::ingredient_id::`` value and merges them
    by summing quantities, combining descriptions, updating one task, and
    deleting the rest.

    This thread is a thin loop wrapper; the actual deduplication logic lives in
    ``GrocerService.deduplicate_items``.
    """
    def __init__(self, service):
        super().__init__(name="grocer-deduplicator")
        self.service = service
        self.daemon = True

    def run(self):
        """Thread entry point."""
        self.service.log.write("[deduplicator] Thread started.")
        while True:
            try:
                self.service.deduplicate_items()
            except Exception as e:
                self.service.log.write("[deduplicator] Error during update: %s" % str(e))

            time.sleep(self.service.config.deduplicator_refresh_rate)
