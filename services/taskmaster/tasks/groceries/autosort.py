# Imports
import os
import sys
from datetime import datetime
import pickle

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig
from tasks.groceries.base import *
import lib.dtu as dtu
from lib.dialogue import DialogueInterface, DialogueAuthor, \
                         DialogueAuthorType

# Helper class used to keep an on-disk record of the current grocery items and
# the categories they've been assigned.
class GrocerySortRecord:
    def __init__(self):
        self.dict = {}
        self.fpath = os.path.join(os.path.realpath(os.path.dirname(__file__)),
                                  ".%s_grocery_sort_record.pkl" % os.path.basename(__file__).replace(".py", ""))

    # Attempts to load from disk. Returns True on success and False on failure.
    def load(self):
        if not os.path.isfile(self.fpath):
            return False
        with open(self.fpath, "rb") as fp:
            self.dict = pickle.load(fp)

    # Attempts to save the dictionary to disk.
    def save(self):
        with open(self.fpath, "wb") as fp:
            pickle.dump(self.dict, fp)

    # Returns the dictionary entry pertaining to the given key. None is
    # returned if the key doesn't exist in the dictionary.
    def get(self, key: str):
        if key not in self.dict:
            return None
        return self.dict[key]

    # Removes the given key and value. Returns True if the key was found, and
    # False if not.
    def remove(self, key: str):
        if key not in self.dict:
            return False
        self.dict.pop(key, None)
        return True

    # Sets the dictionary entry pertaining to the given key.
    def set(self, key: str, data: any):
        self.dict[key] = data

# The main taskjob class.
class TaskJob_Groceries_Autosort(TaskJob_Groceries):
    def init(self):
        self.refresh_rate = 120
        self.gsr = GrocerySortRecord()
        self.gsr.load()

    # Builds a prompt to be passed to an LLM via the dialogue library.
    def build_prompt_intro(self):
        r = "Your job is to sort a list of groceries by category. " \
            "You will be presented with a list of categories and a list of grocery items. " \
            "You must examine each grocery item and assign it a single category from the provided list of categories. " \
            "You must format your response by placing each grocery item, and the category you have assigned it, on its own line. " \
            "Separate the grocery item and its category by a single pipe symbol (\"|\"). " \
            "For example, if the grocery item is \"bananas\" and you have chosen the category \"PRODUCE\", your response must include this line of text: \"bananas|PRODUCE\". " \
            "Include the full list of grocery items and their assigned categories in your response; do not include anything else in your response."
        return r

    # Builds a prompt to be passed to an LLM *after* the initial introduction
    # prompt has been set.
    def build_prompt_message(self, proj, sections, tasks):
        r = ""
        # add the section names as the list of categories
        r += "Here is the list of available categories to choose from:\n"
        for section in sections:
            r += " - \"%s\"\n" % section.name

        # add the grocery items (tasks) to the prompt
        r += "Here is the list of grocery items you must categorize:\n"
        for task in tasks:
            r += " - \"%s\"\n" % task.content
        return r

    def get_task_dict_name(self, task):
        if type(task) != str:
            task = task.content
        return task.strip().lower()

    def get_section_dict_name(self, section):
        if type(section) != str:
            section = section.name
        return section.strip().lower()

    def update(self, todoist, gcal):
        # this task doesn't add any new grocery tasks to the grocery project.
        # Instead, it examines the list and sorts them by category (where each
        # section is a grocery category)

        # retrieve the grocery project; watch out for rate limiting
        proj = None
        rate_limit_retries_attempted = 0
        for attempt in range(self.todoist_rate_limit_retries):
            try:
                proj = self.get_project(todoist)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.log("Getting rate-limited by Todoist. Sleeping...")
                    time.sleep(self.todoist_rate_limit_timeout)
                    rate_limit_retries_attempted += 1
                else:
                    raise e

        # if we exhaused our retries, raise an exception
        if rate_limit_retries_attempted >= self.todoist_rate_limit_retries:
            raise Exception("Exceeded maximum retries due to Todoist rate limiting")

        # first, get all sections in the grocery list (if there are no
        # sections, then sorting is impossible)
        sections = todoist.get_sections(project_id=proj.id)
        if len(sections) == 0:
            return False
        section_dict = {}
        section_id_dict = {}
        for section in sections:
            section_dict[self.get_section_dict_name(section)] = section
            section_id_dict[section.id] = section

        # next, get all tasks (if there are no tasks, then there is nothing to
        # sort). Build a special list of tasks that are either *new*, or are in
        # a different section than they were last time. These are the ones
        # we'll pass to the AI for categorization
        tasks = todoist.get_tasks(project_id=proj.id)
        dirty_tasks = []
        if len(tasks) == 0:
            return False
        task_dict = {}
        for task in tasks:
            tname = self.get_task_dict_name(task)
            task_dict[tname] = task

            # if the task is new, or it's section is different, or it is
            # currently not in any of the sections, add it to the list of dirty
            # tasks
            old_sname = self.gsr.get(tname)
            new_sname = None
            if task.section_id in section_id_dict:
                new_sname = self.get_section_dict_name(section_id_dict[task.section_id])
            if old_sname is None or new_sname != old_sname:
                dirty_tasks.append(task)
                self.log("Grocery item \"%s\" is dirty." % task.content)

        # iterate through the task list. Look for any tasks that no longer
        # exist but are still stored in the GSR. Remove them from the GSR and
        # save it, if any are found
        gsr_keys = list(self.gsr.dict.keys())
        deletions = 0
        for key in gsr_keys:
            if key not in task_dict:
                self.gsr.remove(key)
                deletions += 1
        if deletions > 0:
            self.gsr.save()

        # if there are no "dirty tasks" (i.e. ones that need sorting that
        # differ from the last time we ran this), we're done
        if len(dirty_tasks) == 0:
            return False

        # build the prompt to pass to the AI, as well as a unique author name
        # to be inserted into the dialogue database
        dialogue_intro = self.build_prompt_intro()
        dialogue_message = self.build_prompt_message(proj, sections, dirty_tasks)

        # pass the prompt to the dialogue library
        dialogue = DialogueInterface(self.service.config.dialogue)
        result = dialogue.oneshot(dialogue_intro, dialogue_message)

        # iterate through the response, line-by-line
        delim = "|"
        for line in result.split("\n"):
            # if the line, for some reason, does not have the pipe delimeter,
            # skip it
            if delim not in line:
                continue

            # split the line by the delimeter to get the grocery item and the
            # section name
            pieces = line.split(delim)
            if len(pieces) < 2:
                continue
            tname = pieces[0]
            sname = pieces[1]

            # if the grocery item can't be found in the dictionary, skip it
            tdname = self.get_task_dict_name(tname)
            if tdname not in task_dict:
                continue
            # if the category name can't be found in the dictionary, skip it
            sdname = self.get_section_dict_name(sname)
            if sdname not in section_dict:
                continue

            t = task_dict[tdname]
            s = section_dict[sdname]
            self.log("Moving grocery item \"%s\" to section \"%s\"." %
                     (t.content, s.name))
            todoist.move_task(t.id, section_id=s.id)

            # update the sort record with the new sort information
            self.gsr.set(tdname, sdname)

        # write the sort record out to disk
        self.gsr.save()

        return True

