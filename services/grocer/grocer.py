#!/usr/bin/python3
# The grocer service manages the household grocery list via Todoist.
#
# It provides:
#   - Oracle endpoints for querying and modifying the grocery list
#   - An auto-sorter thread that uses an LLM to categorize grocery items
#   - A recipe-resolver thread that expands recipe references into ingredients
#   - A deduplicator thread that merges duplicate ingredient entries
#
#   Connor Shugg

# Imports
import os
import sys
import flask
import time
import re
import hashlib
import threading
import requests

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.nla import NLAEndpoint, NLAEndpointInvokeParameters, NLAResult
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface
from lib.todoist import Todoist, TodoistConfig

# Thread classes plus the supporting records, constants, and recipe helpers
# they share. The per-iteration business logic lives in GrocerService (below);
# the threads are thin loop wrappers that call into the service methods.
from threads import (
    AutoSorterThread,
    RecipeResolverThread,
    DeduplicatorThread,
    GrocerySortRecord,
    Recipe,
    Ingredient,
    IngredientReplenishType,
    RECIPE_MAGIC_STRING,
    EXPANDED_RECIPE_INGREDIENT_MAGIC,
    RECIPE_RESOLUTION_FAILURE_MAGIC,
    AUTOSORT_IGNORE_MAGIC,
    INGREDIENT_ID_MAGIC,
    TODOIST_RATE_LIMIT_RETRIES,
    TODOIST_RATE_LIMIT_TIMEOUT,
    QUANTITY_RE,
)


# ============================== ReadWriteLock =============================== #
class ReadWriteLock:
    """A readers-writer lock. Multiple readers can hold the lock concurrently,
    but a writer gets exclusive access. Writers are given priority to prevent
    starvation.
    """

    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writing = False

    def acquire_read(self):
        """Acquire a read lock. Multiple threads can hold this
        simultaneously.
        """
        with self._read_ready:
            while self._writing or self._writers_waiting > 0:
                self._read_ready.wait()
            self._readers += 1

    def release_read(self):
        """Release a read lock."""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self):
        """Acquire a write lock. Exclusive access — no other readers or
        writers.
        """
        with self._read_ready:
            self._writers_waiting += 1
            while self._readers > 0 or self._writing:
                self._read_ready.wait()
            self._writers_waiting -= 1
            self._writing = True

    def release_write(self):
        """Release a write lock."""
        with self._read_ready:
            self._writing = False
            self._read_ready.notify_all()


# ================================= Helpers ================================== #
def derive_item_id(task_id: str) -> str:
    """Derives a unique ID string for a grocery item from its Todoist task ID.

    The derived ID is a SHA-256 hex digest of the string "grocery_item_<TASK_ID>".
    This provides a stable, opaque identifier that can be shared externally
    without exposing internal Todoist task IDs.
    """
    return hashlib.sha256(("grocery_item_%s" % task_id).encode()).hexdigest()


# ================================== Config ================================== #
class GrocerConfig(ServiceConfig):
    """Configuration for the grocer service."""
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("todoist",                      [TodoistConfig],        required=True),
            ConfigField("dialogue",                     [DialogueConfig],       required=True),
            ConfigField("chef_oracle",                  [OracleSessionConfig],  required=True),
            ConfigField("autosort_refresh_rate",        [int],  required=False, default=120),
            ConfigField("recipe_resolver_refresh_rate", [int],  required=False, default=120),
            ConfigField("deduplicator_refresh_rate",    [int],  required=False, default=120),
        ]


# ============================== Service Class =============================== #
class GrocerService(Service):
    """Main service class for the grocer.

    The service thread itself is lightweight — it simply sleeps in a loop. All
    real work is performed by the three daemon threads (auto-sorter,
    recipe-resolver, deduplicator) and the Oracle HTTP endpoints.
    """
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = GrocerConfig()
        self.config.parse_file(config_path)

        # Shared readers-writer lock — ALL threads and endpoints must acquire
        # this before making any Todoist API call. Read operations acquire a
        # read lock; write operations acquire a write lock.
        self.todoist_lock = ReadWriteLock()

        # Todoist handle (lazily initialised on first access).
        self._todoist = None

        # Thread instances (set in run()).
        self.autosort_thread = None
        self.recipe_resolver_thread = None
        self.deduplicator_thread = None

        # Persistent auto-sort record — tracks item->section assignments so
        # already-sorted items aren't re-sorted on every iteration.
        self.gsr = GrocerySortRecord()
        self.gsr.load()

    def get_todoist(self) -> Todoist:
        """Returns the shared Todoist API handle, creating it on first call."""
        if self._todoist is None:
            self._todoist = Todoist(self.config.todoist)
        return self._todoist

    def get_grocery_project(self):
        """Returns the "Groceries" Todoist project WITHOUT creating it, or
        ``None`` if it does not exist. This is a read-only lookup: callers may
        hold either a read or write lock. Use `ensure_grocery_project` (or
        `_create_grocery_project_locked` while already holding the write lock)
        to create the project.
        """
        todoist = self.get_todoist()
        return todoist.get_project_by_name("Groceries")

    def _create_grocery_project_locked(self):
        """Returns the "Groceries" Todoist project, creating it if it doesn't
        exist. Performs a write (`add_project`) when the project is missing, so
        callers MUST already hold the WRITE lock before calling this.
        """
        todoist = self.get_todoist()
        proj = todoist.get_project_by_name("Groceries")
        if proj is None:
            proj = todoist.add_project("Groceries", color="green")
        return proj

    def ensure_grocery_project(self):
        """Ensures the "Groceries" Todoist project exists, creating it exactly
        once under the WRITE lock. This is called once at service startup
        (before the worker threads start) so that the read-locked endpoints and
        NLA handlers never need to perform the creating write themselves.
        """
        self.todoist_lock.acquire_write()
        try:
            return self._create_grocery_project_locked()
        finally:
            self.todoist_lock.release_write()

    # ------------------------------ Helpers --------------------------------- #
    def _get_project_with_retry(self, log_prefix: str):
        """Retrieves the Groceries project (read-only) with Todoist rate-limit
        retry. Returns the project, or ``None`` if it does not exist.

        Callers MUST already hold the todoist_lock (read or write). This never
        creates the project; the project is ensured at startup via
        `ensure_grocery_project`.
        """
        for attempt in range(TODOIST_RATE_LIMIT_RETRIES):
            try:
                return self.get_grocery_project()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.log.write("%s Rate-limited by Todoist. Sleeping..." % log_prefix)
                    time.sleep(TODOIST_RATE_LIMIT_TIMEOUT)
                else:
                    raise
        raise Exception("Exceeded maximum retries due to Todoist rate limiting.")

    @staticmethod
    def _task_key(task) -> str:
        """Returns a normalised key for a task (or raw string)."""
        if isinstance(task, str):
            return task.strip().lower()
        return task.content.strip().lower()

    @staticmethod
    def _section_key(section) -> str:
        """Returns a normalised key for a section (or raw string)."""
        if isinstance(section, str):
            return section.strip().lower()
        return section.name.strip().lower()

    def _build_autosort_intro(self) -> str:
        """Builds the system/intro prompt for the auto-sort LLM call."""
        return (
            "Your job is to sort a list of groceries by category. "
            "You will be presented with a list of categories and a list of grocery items. "
            "You must examine each grocery item and assign it a single category from the provided list of categories. "
            "You must format your response by placing each grocery item, and the category you have assigned it, on its own line. "
            "Separate the grocery item and its category by a single pipe symbol (\"|\"). "
            "For example, if the grocery item is \"bananas\" and you have chosen the category \"PRODUCE\", "
            "your response must include this line of text: \"bananas|PRODUCE\". "
            "Include the full list of grocery items and their assigned categories in your response; "
            "do not include anything else in your response."
        )

    def _build_autosort_message(self, sections, dirty_tasks) -> str:
        """Builds the user message listing categories and items."""
        r = "Here is the list of available categories to choose from:\n"
        for section in sections:
            r += " - \"%s\"\n" % section.name
        r += "Here is the list of grocery items you must categorize:\n"
        for task in dirty_tasks:
            r += " - \"%s\"\n" % task.content
        return r

    def _get_chef_session(self) -> OracleSession:
        """Returns an authenticated oracle session to the chef service."""
        s = OracleSession(self.config.chef_oracle)
        try:
            r = s.login()
            if not OracleSession.get_response_success(r):
                msg = OracleSession.get_response_message(r)
                self.log.write("[recipe-resolver] Chef login failed: %s" % msg)
                raise Exception("Chef login failed: %s" % msg)
        except Exception as e:
            self.log.write(
                "[recipe-resolver] Failed to connect to chef service: %s" % str(e)
            )
            raise
        return s

    @staticmethod
    def _extract_ingredient_id(description: str):
        """Searches a task description for the ingredient ID magic string and
        returns the ingredient ID, or ``None`` if not found.
        """
        for line in description.split("\n"):
            line = line.strip()
            if line.startswith(INGREDIENT_ID_MAGIC):
                return line[len(INGREDIENT_ID_MAGIC):]
        return None

    @staticmethod
    def _parse_quantity_and_name(title: str):
        """Parses a title like ``(2x) Ground Beef`` into ``(2.0, "Ground Beef")``.

        If no quantity prefix is found, returns ``(1.0, title)``.
        """
        m = QUANTITY_RE.match(title.strip())
        if m:
            return float(m.group(1)), m.group(2).strip()
        return 1.0, title.strip()

    # --------------------------- Service Methods ---------------------------- #
    def sort_items(self) -> str:
        """Performs one iteration of the auto-sort logic, using an LLM to sort
        unsorted grocery items into the appropriate Todoist section.

        The slow LLM call runs WITHOUT holding any lock. Inputs are gathered
        under a read lock and the resulting Todoist mutations (move/update) are
        applied under a write lock, so read endpoints are not blocked for the
        full LLM round-trip. The read-then-write window is tolerated
        best-effort: a stale task whose mutation fails is logged and skipped
        rather than aborting the whole iteration.

        Returns a status message string.
        """
        try:
            # ---------------- Phase 1: gather inputs (read lock) ------------ #
            self.todoist_lock.acquire_read()
            try:
                todoist = self.get_todoist()

                # Retrieve the grocery project with rate-limit retry.
                proj = self._get_project_with_retry("[autosort]")
                if proj is None:
                    return "Auto-sort completed successfully."

                # Fetch sections. No sections -> nothing to sort.
                sections = todoist.get_sections(project_id=proj.id)
                if len(sections) == 0:
                    return "Auto-sort completed successfully."

                section_dict = {}      # normalised name -> section object
                section_id_dict = {}   # section ID -> section object
                for s in sections:
                    section_dict[self._section_key(s)] = s
                    section_id_dict[s.id] = s

                # Fetch all tasks in the grocery project.
                tasks = todoist.get_tasks(project_id=proj.id)
                if len(tasks) == 0:
                    return "Auto-sort completed successfully."

                # Determine which tasks are "dirty" (need sorting), and which
                # recipe-reference tasks need the autosort-ignore marker added.
                dirty_tasks = []
                ignore_updates = []   # [(task_id, new_desc), ...]
                task_dict = {}
                for task in tasks:
                    tname = self._task_key(task)
                    task_dict[tname] = task

                    # Skip tasks that look like recipe references (the
                    # recipe-resolver handles those).
                    task_title = task.content.strip().lower()
                    task_description = task.description.strip().lower() if task.description else ""
                    if RECIPE_MAGIC_STRING in task_title:
                        if AUTOSORT_IGNORE_MAGIC not in task_description:
                            # Mark the task so the user knows autosort skips it.
                            new_desc = task.description if task.description else ""
                            new_desc += "\n%s" % AUTOSORT_IGNORE_MAGIC
                            ignore_updates.append((task.id, new_desc))
                        continue

                    # Check whether this task has already been sorted to the
                    # same section as recorded.
                    old_sname = self.gsr.get(tname)
                    new_sname = None
                    if task.section_id in section_id_dict:
                        new_sname = self._section_key(section_id_dict[task.section_id])
                    if old_sname is None or new_sname != old_sname:
                        dirty_tasks.append(task)
                        self.log.write("[autosort] Grocery item \"%s\" is dirty." % task.content)

                # Compute stale sort-record keys (entries for tasks that no
                # longer exist). The actual removal happens under the write
                # lock below.
                stale_keys = [key for key in self.gsr.dict.keys()
                              if key not in task_dict]

                # Build the LLM prompt inputs while we still hold the read lock.
                if len(dirty_tasks) > 0:
                    dialogue_intro = self._build_autosort_intro()
                    dialogue_message = self._build_autosort_message(sections, dirty_tasks)
            finally:
                self.todoist_lock.release_read()

            # ---------- Phase 2: slow LLM categorisation (no lock) ---------- #
            # Parse the LLM response into a list of moves keyed by the
            # normalised task/section names gathered above.
            moves = []  # [(task_id, task_content, section_id, section_name, tdname, sdname), ...]
            if len(dirty_tasks) > 0:
                dialogue = DialogueInterface(self.config.dialogue)
                result = dialogue.oneshot(dialogue_intro, dialogue_message)

                delim = "|"
                for line in result.split("\n"):
                    if delim not in line:
                        continue
                    pieces = line.split(delim)
                    if len(pieces) < 2:
                        continue

                    tdname = self._task_key(pieces[0])
                    if tdname not in task_dict:
                        continue
                    sdname = self._section_key(pieces[1])
                    if sdname not in section_dict:
                        continue

                    t = task_dict[tdname]
                    s = section_dict[sdname]
                    moves.append((t.id, t.content, s.id, s.name, tdname, sdname))

            # ------------- Phase 3: apply mutations (write lock) ------------ #
            if len(ignore_updates) == 0 and len(stale_keys) == 0 and len(moves) == 0:
                return "Auto-sort completed successfully."

            self.todoist_lock.acquire_write()
            try:
                todoist = self.get_todoist()

                # Add the autosort-ignore marker to recipe tasks (best-effort).
                for task_id, new_desc in ignore_updates:
                    try:
                        todoist.update_task(task_id, body=new_desc)
                    except Exception as e:
                        self.log.write(
                            "[autosort] Failed to mark task %s as ignored "
                            "(may have changed): %s" % (task_id, str(e))
                        )

                # Prune stale entries from the sort record.
                record_dirty = False
                for key in stale_keys:
                    if self.gsr.remove(key):
                        record_dirty = True

                # Apply the moves computed from the LLM response (best-effort).
                for task_id, task_content, section_id, section_name, tdname, sdname in moves:
                    try:
                        self.log.write(
                            "[autosort] Moving \"%s\" to section \"%s\"." %
                            (task_content, section_name)
                        )
                        todoist.move_task(task_id, section_id=section_id)
                        self.gsr.set(tdname, sdname)
                        record_dirty = True
                    except Exception as e:
                        self.log.write(
                            "[autosort] Failed to move task %s (may have "
                            "changed): %s" % (task_id, str(e))
                        )

                if record_dirty:
                    self.gsr.save()
            finally:
                self.todoist_lock.release_write()

            return "Auto-sort completed successfully."
        except Exception as e:
            return "Auto-sort failed: %s" % str(e)

    def resolve_recipes(self) -> str:
        """Performs one iteration of recipe resolution, expanding recipe
        references in the grocery list into individual ingredient tasks via the
        chef service.

        The chef HTTP calls run WITHOUT holding any lock. The grocery tasks are
        gathered under a read lock and the resulting Todoist mutations
        (add/update/delete) are applied under a write lock, so read endpoints
        are not blocked across the (potentially many) chef round-trips. The
        read-then-write window is tolerated best-effort: a stale original task
        whose delete/update fails is logged and skipped.

        Returns a status message string.
        """
        try:
            # ---------------- Phase 1: gather inputs (read lock) ------------ #
            self.todoist_lock.acquire_read()
            try:
                todoist = self.get_todoist()
                proj = self._get_project_with_retry("[recipe-resolver]")
                if proj is None:
                    return "Recipe resolution completed successfully."
                project_id = proj.id
                tasks = todoist.get_tasks(project_id=project_id)
            finally:
                self.todoist_lock.release_read()

            if len(tasks) == 0:
                return "Recipe resolution completed successfully."

            # ---------- Phase 2: chef resolution + build actions ------------ #
            # No lock is held while we talk to the chef service. We accumulate
            # the Todoist mutations to perform and apply them under the write
            # lock afterwards.
            adds = []             # [(title, description), ...]
            failure_updates = []  # [(task_id, new_title, new_desc), ...]
            deletes = []          # [task_id, ...]

            chef = self._get_chef_session()
            for task in tasks:
                task_title = task.content.strip().lower()
                task_description = (task.description.strip().lower()
                                    if task.description else "")

                # Skip tasks already processed.
                keywords_to_skip = [
                    EXPANDED_RECIPE_INGREDIENT_MAGIC,
                    RECIPE_RESOLUTION_FAILURE_MAGIC,
                ]
                skip = False
                for kw in keywords_to_skip:
                    if kw in task_title or kw in task_description:
                        skip = True
                if skip:
                    continue

                # Only process tasks that contain the recipe magic string.
                if RECIPE_MAGIC_STRING not in task_title:
                    continue

                self.log.write(
                    "[recipe-resolver] Found recipe reference: \"%s\". Resolving..." % task.content
                )

                # Resolve the recipe via the chef service.
                text_to_resolve = task_title
                if task.description and len(task.description.strip()) > 0:
                    text_to_resolve += "\n\n" + task.description.strip()

                chef_result = chef.post("/recipes/resolve", {"text": text_to_resolve})
                chef_status = OracleSession.get_response_status(chef_result)
                if chef_status != 200:
                    self.log.write(
                        "[recipe-resolver] Failed to resolve recipe: status %d" % chef_status
                    )
                    if chef_status == 404:
                        # Mark the task so the user knows resolution failed.
                        new_title = "❓ %s" % task.content
                        new_desc = "(Could not find a matching recipe)\n\n"
                        if task.description and len(task.description.strip()) > 0:
                            new_desc += task.description
                        new_desc += "\n\n%s" % RECIPE_RESOLUTION_FAILURE_MAGIC

                        failure_updates.append((task.id, new_title, new_desc))
                    continue

                recipe_info = OracleSession.get_response_json(chef_result)

                # Fetch full recipe details.
                chef_result = chef.post("/recipes/get_by_id", {"id": recipe_info["id"]})
                if OracleSession.get_response_status(chef_result) != 200:
                    msg = OracleSession.get_response_message(chef_result)
                    self.log.write(
                        "[recipe-resolver] Failed to fetch recipe "
                        "details for \"%s\": status=%d, msg=%s" % (
                            recipe_info["id"],
                            OracleSession.get_response_status(
                                chef_result
                            ),
                            msg,
                        )
                    )
                    continue

                recipe = Recipe.from_json(OracleSession.get_response_json(chef_result))
                self.log.write(
                    "[recipe-resolver] Resolved \"%s\" → recipe \"%s\"." % (task.content, recipe.id)
                )

                # Build ingredient list with quantity adjustments.
                quantity_multiplier = float(recipe_info.get("quantity", 1.0))
                ingredients = []
                for ingredient in recipe.ingredients:
                    i_json = ingredient.to_json()
                    i_json["quantity"] = float(i_json.get("quantity", 1.0)) * quantity_multiplier
                    ingredients.append(Ingredient.from_json(i_json))

                # Build each ingredient task (added to Todoist in phase 3).
                for ingredient in ingredients:
                    title = ingredient.title if ingredient.title is not None else ingredient.id

                    # Show quantity in the title using the (Nx) format.
                    qty = ingredient.quantity
                    if qty != 1.0:
                        if int(qty) == qty:
                            title = "(%dx) %s" % (int(qty), title)
                        else:
                            title = "(%.2fx) %s" % (qty, title)

                    # Build the description.
                    description = ""
                    if ingredient.is_optional:
                        description += "(OPTIONAL) "
                    if ingredient.replenish == IngredientReplenishType.SOMETIMES:
                        description += "(❗ You may already have this) "
                    elif ingredient.replenish == IngredientReplenishType.RARELY:
                        description += "(‼️  You probably already have this) "

                    recipe_str = recipe.title if recipe.title is not None else recipe.id
                    icon_str = ("%s " % recipe.icon) if recipe.icon is not None else ""
                    description += "[%s%s]" % (icon_str, recipe_str)

                    if quantity_multiplier != 1.0:
                        serving_count = recipe.servings * quantity_multiplier
                        if int(serving_count) == serving_count:
                            description += " - %d servings" % int(serving_count)
                        else:
                            description += " - %.2f servings" % serving_count

                    if ingredient.description is not None:
                        description += "\n\n%s" % ingredient.description

                    # Add the magic string to prevent re-resolution.
                    description += "\n\n%s" % EXPANDED_RECIPE_INGREDIENT_MAGIC

                    # CRITICAL: embed the chef ingredient ID for the deduplicator.
                    description += "\n%s%s" % (INGREDIENT_ID_MAGIC, ingredient.id)

                    adds.append((title, description))

                # Schedule deletion of the original recipe-reference task.
                deletes.append(task.id)

            # ------------- Phase 3: apply mutations (write lock) ------------ #
            if len(adds) == 0 and len(failure_updates) == 0 and len(deletes) == 0:
                return "Recipe resolution completed successfully."

            self.todoist_lock.acquire_write()
            try:
                todoist = self.get_todoist()

                # Mark recipes that could not be resolved (best-effort).
                for task_id, new_title, new_desc in failure_updates:
                    try:
                        todoist.update_task(task_id, title=new_title, body=new_desc)
                    except Exception as e:
                        self.log.write(
                            "[recipe-resolver] Failed to mark task %s as "
                            "unresolved (may have changed): %s" % (task_id, str(e))
                        )

                # Add the expanded ingredient tasks (best-effort).
                for title, description in adds:
                    try:
                        todoist.add_task(title, description, project_id=project_id)
                        self.log.write(
                            "[recipe-resolver] Added ingredient \"%s\"." % title
                        )
                    except Exception as e:
                        self.log.write(
                            "[recipe-resolver] Failed to add ingredient "
                            "\"%s\": %s" % (title, str(e))
                        )

                # Delete the original recipe-reference tasks (best-effort).
                tasks_resolved = 0
                for task_id in deletes:
                    try:
                        todoist.delete_task(task_id)
                        tasks_resolved += 1
                    except Exception as e:
                        self.log.write(
                            "[recipe-resolver] Failed to remove original "
                            "recipe task %s (may have changed): %s" % (task_id, str(e))
                        )

                if tasks_resolved > 0:
                    self.log.write(
                        "[recipe-resolver] Resolved %d recipe(s) this cycle." % tasks_resolved
                    )
            finally:
                self.todoist_lock.release_write()

            return "Recipe resolution completed successfully."
        except Exception as e:
            return "Recipe resolution failed: %s" % str(e)

    def deduplicate_items(self) -> str:
        """Performs one iteration of deduplication, merging grocery items that
        share the same ``dimrod::ingredient_id::`` value by summing quantities,
        combining descriptions, and deleting the redundant tasks.

        Inputs are gathered under a read lock and the CPU-only merge work is
        done without any lock; only the Todoist mutations (update/delete) run
        under the write lock. The read-then-write window is tolerated
        best-effort: a stale task whose update/delete fails is logged and
        skipped.

        Returns a status message string.
        """
        try:
            # ---------------- Phase 1: gather inputs (read lock) ------------ #
            self.todoist_lock.acquire_read()
            try:
                todoist = self.get_todoist()
                proj = self._get_project_with_retry("[deduplicator]")
                if proj is None:
                    return "Deduplication completed successfully."
                tasks = todoist.get_tasks(project_id=proj.id)
            finally:
                self.todoist_lock.release_read()

            if len(tasks) == 0:
                return "Deduplication completed successfully."

            # ---------- Phase 2: compute merges (CPU only, no lock) --------- #
            # Group tasks by their ingredient ID (only tasks that have one).
            groups = {}  # ingredient_id -> [task, ...]
            for task in tasks:
                desc = task.description if task.description else ""
                iid = self._extract_ingredient_id(desc)
                if iid is None:
                    continue
                groups.setdefault(iid, []).append(task)

            # Build the merge actions for each group with duplicates.
            merges = []  # [(keep_task_id, merged_title, merged_desc, [delete_id, ...]), ...]
            for iid, group_tasks in groups.items():
                if len(group_tasks) < 2:
                    continue

                self.log.write(
                    "[deduplicator] Found %d duplicates for ingredient ID \"%s\"." %
                    (len(group_tasks), iid)
                )

                # Parse quantities and base names from each task title.
                total_qty = 0.0
                base_name = None
                descriptions = []
                for t in group_tasks:
                    qty, name = self._parse_quantity_and_name(t.content)
                    total_qty += qty
                    if base_name is None:
                        base_name = name
                    if t.description:
                        descriptions.append(t.description.strip())

                # Build the merged title.
                if int(total_qty) == total_qty and total_qty != 1.0:
                    merged_title = "(%dx) %s" % (int(total_qty), base_name)
                elif total_qty != 1.0:
                    merged_title = "(%.2fx) %s" % (total_qty, base_name)
                else:
                    merged_title = base_name

                # Build the merged description — deduplicate identical lines.
                seen_lines = set()
                merged_desc_lines = []
                for desc in descriptions:
                    for line in desc.split("\n"):
                        stripped = line.strip()
                        if stripped and stripped not in seen_lines:
                            seen_lines.add(stripped)
                            merged_desc_lines.append(stripped)
                merged_desc = "\n".join(merged_desc_lines)

                # Update the first task, delete the rest.
                keep_task = group_tasks[0]
                delete_ids = [dt.id for dt in group_tasks[1:]]
                merges.append((keep_task.id, merged_title, merged_desc,
                               delete_ids, len(group_tasks)))

            # ------------- Phase 3: apply mutations (write lock) ------------ #
            if len(merges) == 0:
                return "Deduplication completed successfully."

            self.todoist_lock.acquire_write()
            try:
                todoist = self.get_todoist()
                for keep_id, merged_title, merged_desc, delete_ids, group_size in merges:
                    # Update the kept task; if this fails (e.g. it was deleted
                    # since we read it), skip the whole group rather than
                    # deleting the duplicates and losing the item entirely.
                    try:
                        todoist.update_task(keep_id, title=merged_title, body=merged_desc)
                    except Exception as e:
                        self.log.write(
                            "[deduplicator] Failed to update kept task %s (may "
                            "have changed); skipping group: %s" % (keep_id, str(e))
                        )
                        continue

                    for dt_id in delete_ids:
                        try:
                            todoist.delete_task(dt_id)
                        except Exception as e:
                            self.log.write(
                                "[deduplicator] Failed to delete duplicate task "
                                "%s (may have changed): %s" % (dt_id, str(e))
                            )

                    self.log.write(
                        "[deduplicator] Merged %d items → \"%s\"." % (group_size, merged_title)
                    )
            finally:
                self.todoist_lock.release_write()

            return "Deduplication completed successfully."
        except Exception as e:
            return "Deduplication failed: %s" % str(e)

    def run(self):
        """Overridden main function. Launches the three worker threads, then
        sleeps forever (the Oracle and daemon threads do the real work).
        """
        super().run()

        # Ensure the "Groceries" Todoist project exists exactly once, under the
        # write lock, BEFORE the worker threads start. This way the read-locked
        # endpoints and NLA handlers never have to perform the creating write
        # themselves (which would race two concurrent readers). See I-1.
        try:
            self.ensure_grocery_project()
        except Exception as e:
            # Non-fatal: log and continue. The write-locked add endpoints can
            # still lazily create the project later if needed.
            self.log.write("Failed to ensure the Groceries project exists: %s" % str(e))

        # Create and store thread instances. The threads are thin loop
        # wrappers — all business logic lives in the GrocerService methods
        # they call (sort_items, resolve_recipes, deduplicate_items).
        self.autosort_thread = AutoSorterThread(self)
        self.recipe_resolver_thread = RecipeResolverThread(self)
        self.deduplicator_thread = DeduplicatorThread(self)

        # Launch as daemons so they die with the service process.
        threads = [
            self.autosort_thread,
            self.recipe_resolver_thread,
            self.deduplicator_thread,
        ]
        for t in threads:
            t.daemon = True
            t.start()
            self.log.write("Started thread: %s" % t.name)

        # Sleep forever — threads and Oracle handle all work.
        while True:
            time.sleep(60)


# ============================== Service Oracle ============================== #
class GrocerOracle(Oracle):
    """HTTP oracle for the grocer service."""

    def endpoints(self):
        """Register all Oracle HTTP endpoints."""
        super().endpoints()

        # ------------------------------------------------------------------ #
        # POST /categories — list all sections in the Groceries project
        # ------------------------------------------------------------------ #
        @self.server.route("/categories", methods=["POST"])
        def endpoint_categories():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            try:
                self.service.todoist_lock.acquire_read()
                try:
                    todoist = self.service.get_todoist()
                    proj = self.service.get_grocery_project()
                    sections = todoist.get_sections(project_id=proj.id) \
                        if proj is not None else []
                finally:
                    self.service.todoist_lock.release_read()

                result = []
                for s in sections:
                    result.append({
                        "id": s.id,
                        "name": s.name,
                    })
                return self.make_response(payload=result)
            except Exception as e:
                return self.make_response(msg="Failed to retrieve categories: %s" % str(e),
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /items — list all grocery items with derived IDs
        # ------------------------------------------------------------------ #
        @self.server.route("/items", methods=["POST"])
        def endpoint_items():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            try:
                self.service.todoist_lock.acquire_read()
                try:
                    todoist = self.service.get_todoist()
                    proj = self.service.get_grocery_project()
                    tasks = todoist.get_tasks(project_id=proj.id) \
                        if proj is not None else []
                finally:
                    self.service.todoist_lock.release_read()

                result = []
                for t in tasks:
                    result.append({
                        "id": derive_item_id(t.id),
                        "task_id": t.id,
                        "title": t.content,
                        "description": t.description,
                        "section_id": t.section_id,
                    })
                return self.make_response(payload=result)
            except Exception as e:
                return self.make_response(msg="Failed to retrieve items: %s" % str(e),
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /items/add — add a new grocery item
        # ------------------------------------------------------------------ #
        @self.server.route("/items/add", methods=["POST"])
        def endpoint_items_add():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            title = flask.g.jdata.get("title", None)
            if not title:
                return self.make_response(msg="Missing required field \"title\".",
                                          success=False, rstatus=400)

            description = flask.g.jdata.get("description", "")
            section_id = flask.g.jdata.get("section_id", None)

            try:
                self.service.todoist_lock.acquire_write()
                try:
                    todoist = self.service.get_todoist()
                    proj = self.service._create_grocery_project_locked()
                    task = todoist.add_task(
                        title,
                        description,
                        project_id=proj.id,
                        section_id=section_id,
                    )
                finally:
                    self.service.todoist_lock.release_write()

                return self.make_response(payload={
                    "id": derive_item_id(task.id),
                    "task_id": task.id,
                    "title": task.content,
                })
            except Exception as e:
                return self.make_response(msg="Failed to add item: %s" % str(e),
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /items/remove — remove a grocery item by its derived ID
        # ------------------------------------------------------------------ #
        @self.server.route("/items/remove", methods=["POST"])
        def endpoint_items_remove():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            item_id = flask.g.jdata.get("id", None)
            if not item_id:
                return self.make_response(msg="Missing required field \"id\".",
                                          success=False, rstatus=400)

            try:
                self.service.todoist_lock.acquire_write()
                try:
                    todoist = self.service.get_todoist()
                    proj = self.service._create_grocery_project_locked()
                    tasks = todoist.get_tasks(project_id=proj.id)

                    # Find the task whose derived ID matches the one given.
                    target_task = None
                    for t in tasks:
                        if derive_item_id(t.id) == item_id:
                            target_task = t
                            break

                    if target_task is None:
                        return self.make_response(
                            msg="No grocery item found with ID \"%s\"." % item_id,
                            success=False, rstatus=404,
                        )

                    todoist.delete_task(target_task.id)
                finally:
                    self.service.todoist_lock.release_write()

                return self.make_response(
                    msg="Removed item \"%s\"." % target_task.content,
                )
            except Exception as e:
                return self.make_response(msg="Failed to remove item: %s" % str(e),
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /items/sort — trigger on-demand auto-sort
        # ------------------------------------------------------------------ #
        @self.server.route("/items/sort", methods=["POST"])
        def endpoint_items_sort():
            """Triggers an on-demand auto-sort of grocery items."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            result = self.service.sort_items()
            success = "failed" not in result.lower()
            return self.make_response(
                success=success,
                msg=result,
                rstatus=200 if success else 500,
            )

        # ------------------------------------------------------------------ #
        # POST /items/resolve_recipes — trigger on-demand recipe resolution
        # ------------------------------------------------------------------ #
        @self.server.route("/items/resolve_recipes", methods=["POST"])
        def endpoint_items_resolve_recipes():
            """Triggers on-demand recipe resolution."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            result = self.service.resolve_recipes()
            success = "failed" not in result.lower()
            return self.make_response(
                success=success,
                msg=result,
                rstatus=200 if success else 500,
            )

        # ------------------------------------------------------------------ #
        # POST /items/deduplicate — trigger on-demand deduplication
        # ------------------------------------------------------------------ #
        @self.server.route("/items/deduplicate", methods=["POST"])
        def endpoint_items_deduplicate():
            """Triggers on-demand deduplication of grocery items."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            result = self.service.deduplicate_items()
            success = "failed" not in result.lower()
            return self.make_response(
                success=success,
                msg=result,
                rstatus=200 if success else 500,
            )

    # ------------------------------ NLA Setup ------------------------------- #
    def init_nla(self):
        """Registers NLA endpoints for the grocer service."""
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                "name": "list_categories",
                "description": "List grocery categories (sections) in the grocery list. "
                               "Phrases like \"what grocery categories are there?\", "
                               "\"list grocery sections\", \"show me the categories\", etc.",
            }).set_handler(nla_list_categories),
            NLAEndpoint.from_json({
                "name": "list_grocery_items",
                "description": "List all items currently on the grocery list. "
                               "Phrases like \"what's on the grocery list?\", "
                               "\"show me the groceries\", \"list grocery items\", etc.",
            }).set_handler(nla_list_grocery_items),
            NLAEndpoint.from_json({
                "name": "add_grocery_item",
                "description": "Add an item to the grocery list. "
                               "Phrases like \"add milk to the grocery list\", "
                               "\"put bananas on the groceries\", \"I need eggs\", etc.",
            }).set_handler(nla_add_grocery_item),
            NLAEndpoint.from_json({
                "name": "remove_grocery_item",
                "description": "Remove an item from the grocery list. "
                               "Phrases like \"remove milk from the grocery list\", "
                               "\"take bananas off the groceries\", "
                               "\"delete eggs from the list\", etc.",
            }).set_handler(nla_remove_grocery_item),
        ]


# =============================== NLA Handlers =============================== #
def nla_list_categories(oracle, jdata):
    """NLA handler that lists all grocery categories (Todoist sections)."""
    try:
        oracle.service.todoist_lock.acquire_read()
        try:
            todoist = oracle.service.get_todoist()
            proj = oracle.service.get_grocery_project()
            sections = todoist.get_sections(project_id=proj.id) \
                if proj is not None else []
        finally:
            oracle.service.todoist_lock.release_read()

        if len(sections) == 0:
            return NLAResult.from_json({
                "success": True,
                "message": "There are no grocery categories set up yet.",
            })

        lines = []
        for s in sorted(sections, key=lambda s: s.name):
            lines.append("· %s" % s.name)

        msg = "Grocery categories:\n" + "\n".join(lines)
        return NLAResult.from_json({
            "success": True,
            "message": msg,
        })
    except Exception as e:
        return NLAResult.from_json({
            "success": False,
            "message": "Failed to list categories: %s" % str(e),
        })


def nla_list_grocery_items(oracle, jdata):
    """NLA handler that lists all items on the grocery list."""
    try:
        oracle.service.todoist_lock.acquire_read()
        try:
            todoist = oracle.service.get_todoist()
            proj = oracle.service.get_grocery_project()
            if proj is not None:
                tasks = todoist.get_tasks(project_id=proj.id)
                sections = todoist.get_sections(project_id=proj.id)
            else:
                tasks = []
                sections = []
        finally:
            oracle.service.todoist_lock.release_read()

        if len(tasks) == 0:
            return NLAResult.from_json({
                "success": True,
                "message": "The grocery list is empty.",
            })

        # Build a section-ID-to-name lookup.
        section_map = {}
        for s in sections:
            section_map[s.id] = s.name

        lines = []
        for t in tasks:
            did = derive_item_id(t.id)
            section_name = section_map.get(t.section_id, "Unsorted")
            lines.append("· %s [%s] (%s)" % (t.content, section_name, did[:8]))

        msg = "Grocery list (%d item%s):\n%s" % (
            len(tasks),
            "s" if len(tasks) != 1 else "",
            "\n".join(lines),
        )
        return NLAResult.from_json({
            "success": True,
            "message": msg,
        })
    except Exception as e:
        return NLAResult.from_json({
            "success": False,
            "message": "Failed to list grocery items: %s" % str(e),
        })


def nla_add_grocery_item(oracle, jdata):
    """NLA handler that adds an item to the grocery list.

    Parses the item title from the user's message. The message itself is used
    as the item title after stripping common preamble phrases.
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)

    # Use the substring if available; otherwise fall back to the full message.
    user_text = params.message
    if params.has_substring():
        user_text = params.substring

    # Strip common preamble phrases to extract the item name.
    item_title = _extract_item_name(user_text)
    if not item_title or len(item_title.strip()) == 0:
        return NLAResult.from_json({
            "success": False,
            "message": "I could not determine which item to add. "
                       "Please specify the item name.",
        })

    try:
        oracle.service.todoist_lock.acquire_write()
        try:
            todoist = oracle.service.get_todoist()
            proj = oracle.service._create_grocery_project_locked()
            task = todoist.add_task(
                item_title.strip(),
                "",
                project_id=proj.id,
            )
        finally:
            oracle.service.todoist_lock.release_write()

        return NLAResult.from_json({
            "success": True,
            "message": "Added \"%s\" to the grocery list." % task.content,
        })
    except Exception as e:
        return NLAResult.from_json({
            "success": False,
            "message": "Failed to add item: %s" % str(e),
        })


def nla_remove_grocery_item(oracle, jdata):
    """NLA handler that removes an item from the grocery list.

    Parses the item name from the user's message, then searches for a matching
    task by title (case-insensitive substring match).
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)

    user_text = params.message
    if params.has_substring():
        user_text = params.substring

    item_name = _extract_item_name(user_text)
    if not item_name or len(item_name.strip()) == 0:
        return NLAResult.from_json({
            "success": False,
            "message": "I could not determine which item to remove. "
                       "Please specify the item name.",
        })

    try:
        oracle.service.todoist_lock.acquire_write()
        try:
            todoist = oracle.service.get_todoist()
            proj = oracle.service._create_grocery_project_locked()
            tasks = todoist.get_tasks(project_id=proj.id)

            # Search for the item by case-insensitive substring match.
            target = None
            item_lower = item_name.strip().lower()
            for t in tasks:
                if item_lower in t.content.strip().lower():
                    target = t
                    break

            if target is None:
                return NLAResult.from_json({
                    "success": False,
                    "message": "Could not find \"%s\" on the grocery list." % item_name,
                })

            todoist.delete_task(target.id)
        finally:
            oracle.service.todoist_lock.release_write()

        return NLAResult.from_json({
            "success": True,
            "message": "Removed \"%s\" from the grocery list." % target.content,
        })
    except Exception as e:
        return NLAResult.from_json({
            "success": False,
            "message": "Failed to remove item: %s" % str(e),
        })


# =============================== NLA Helpers ================================ #
# Regex patterns used to strip common "add/remove" preamble from NLA messages
# so we can extract the actual grocery item name.
_PREAMBLE_PATTERNS = [
    re.compile(r"^(?:please\s+)?(?:add|put|place)\s+(.+?)(?:\s+(?:to|on|onto)\s+(?:the\s+)?(?:grocery|groceries|shopping)\s*(?:list)?)?\s*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:remove|delete|take)\s+(.+?)(?:\s+(?:from|off|off\s+of)\s+(?:the\s+)?(?:grocery|groceries|shopping)\s*(?:list)?)?\s*$", re.IGNORECASE),
    re.compile(r"^(?:i\s+need|we\s+need|get|buy|grab)\s+(.+?)$", re.IGNORECASE),
]


def _extract_item_name(text: str) -> str:
    """Attempts to extract a grocery item name from natural-language text by
    stripping common preamble phrases. Returns the raw text if no pattern
    matches (the caller may still use it directly).
    """
    text = text.strip()
    for pattern in _PREAMBLE_PATTERNS:
        m = pattern.match(text)
        if m:
            return m.group(1).strip()
    return text


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=GrocerConfig, service=GrocerService, oracle=GrocerOracle)
    cli.run()
