# This module implements classes used to create custom task scripts to decide
# when to create and update tasks.

# Imports
import os
import sys
import pickle
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(fdir)
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# A config class used to pull in task information from external files.
class TaskConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("title",        [str],  required=True),
            ConfigField("content",      [str],  required=True),
            ConfigField("priority",     [int],  required=False, default=1),
            ConfigField("labels",       [list], required=False, default=[]),
            ConfigField("project_name", [str],  required=False, default=None),
            ConfigField("section_name", [str],  required=False, default=None)
        ]
    
    # Returns the task's content. The content will either be the string
    # given in 'content', or, if 'content' is a valid file path, the
    # contents of the specified file.
    def get_content(self):
        if os.path.isfile(self.content):
            with open(self.content, "r") as fp:
                return fp.read()
        return self.content

# A class for running custom conditions and interacting with Todoist to
# add/update tasks.
class TaskJob:
    def __init__(self, service):
        self.service = service
        self.last_success_fpath = os.path.join(fdir, ".%s_last_success.pkl" %
                                               self.__class__.__name__.lower())

    # Function that uses the todoist API to update any tasks.
    # Must be implemented by subclasses.
    # Must return True if the update succeeded and Todoist was updated in some
    # way. Otherwise, must return False.
    def update(self, todoist):
        return False
    
    # Writes a log message specific to this TaskJob to the service' log.
    def log(self, msg: str):
        pfx = "[%s]" % self.__class__.__name__
        self.service.log.write("%s %s" % (pfx, msg))
    
    # Saves a given timestamp to disk to reference as the last time the task
    # job's update() function succeeded in adding/updating tasks or modified
    # Todoist in some way.
    def set_last_success_datetime(self, dt: datetime):
        with open(self.last_success_fpath, "wb") as fp:
            pickle.dump(dt, fp)
    
    # Returns the last time the task job's update() function succeeded. The
    # value is returned from disk. Returns None if no record has been saved
    # yet.
    def get_last_success_datetime(self):
        if not os.path.isfile(self.last_success_fpath):
            return None
        with open(self.last_success_fpath, "rb") as fp:
            return pickle.load(fp)
    
    # Returns the name of the task job.
    def get_name(self):
        return self.__class__.__name__.replace("taskjob_", "").lower()
    
    # Creates and returns the project with the given name.
    def get_project_by_name(self, todoist, name: str,
                            color="grey", parent_id=None,
                            is_favorite=False, view_style="list"):
        proj = todoist.get_project_by_name(name)
        if proj is None:
            proj = todoist.add_project(name, color=color, parent_id=None)
        return proj

    # Creates and returns the section with the given name.
    def get_section_by_name(self, todoist, project_id: str, name: str,
                            order=None):
        sect = todoist.get_section_by_name(name)
        if sect is None:
            sect = todoist.add_section(name, project_id, order=None)
        return sect


