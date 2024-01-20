# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig
from tasks.base import *
import lib.dtu as dtu
from lib.dialogue import DialogueConfig, DialogueInterface, DialogueAuthor, DialogueAuthorType

class TaskJob_Groceries_Autosort(TaskJob_Groceries):
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
    

    def update(self, todoist):
        # this task doesn't add any new grocery tasks to the grocery project.
        # Instead, it examines the list and sorts them by category (where each
        # section is a grocery category)
        proj = self.get_project(todoist)

        # first, get all sections in the grocery list (if there are no
        # sections, then sorting is impossible)
        sections = todoist.get_sections(project_id=proj.id)
        if len(sections) == 0:
            return False
        section_dict = {}
        for section in sections:
            section_dict[section.name.strip().lower()] = section

        # next, get all tasks (if there are no tasks, then there is nothing to
        # sort)
        tasks = todoist.get_tasks(project_id=proj.id)
        if len(tasks) == 0:
            return False
        task_dict = {}
        for task in tasks:
            task_dict[task.content.strip().lower()] = task

        # build the prompt to pass to the AI, as well as a unique author name
        # to be inserted into the dialogue database
        dialogue_intro = self.build_prompt_intro()
        dialogue_message = self.build_prompt_message(proj, sections, tasks)
        dialogue_author = DialogueAuthor("taskmaster_%s" % __class__.__name__.lower(),
                                         DialogueAuthorType.SYSTEM)

        # pass the prompt to the dialogue library
        dialogue_config = DialogueConfig()
        dialogue_config.parse_json(self.service.config.to_json())
        dialogue = DialogueInterface(dialogue_config)
        c = dialogue.talk(dialogue_message, author=dialogue_author, intro=dialogue_intro)
        result = c.latest_response().content
        
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
            tdname = tname.strip().lower()
            if tdname not in task_dict:
                continue
            # if the category name can't be found in the dictionary, skip it
            sdname = sname.strip().lower()
            if sdname not in section_dict:
                continue

            t = task_dict[tdname]
            s = section_dict[sdname]
            self.log("Moving grocery item \"%s\" to section \"%s\"." %
                     (t.content, s.name))
            todoist.move_task(t.id, section_id=s.id)
            
        return True

