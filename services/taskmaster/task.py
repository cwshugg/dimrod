# This module implements classes used to create custom task scripts to decide
# when to create and update tasks.

# Imports
import os
import sys
import pickle
from datetime import datetime
import requests
import time

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
            ConfigField("section_name", [str],  required=False, default=None),
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
        self.refresh_rate = 43200
        self.todoist_rate_limit_timeout = 30
        self.last_update_fpath = os.path.join(fdir, ".%s_last_update.pkl" %
                                              self.__class__.__name__.lower())
        self.last_success_fpath = os.path.join(fdir, ".%s_last_success.pkl" %
                                               self.__class__.__name__.lower())
        self.init()
    
    # Function that can be optionally overridden by subclasses to run
    # initialization code before any calls to the taskjob's "update()" are
    # made.
    def init(self):
        pass
    
    # Function that uses the provided API objects to update any tasks, events,
    # etc. This must be implemented by subclasses.
    # Must return True if the update succeeded (some sort of update was made).
    # Otherwise, must return False.
    def update(self, todoist, gcal):
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
    
    # Saves a given timestamp to disk to reference as the last time the task
    # job's update() function was executed (either failing or succeeding).
    def set_last_update_datetime(self, dt: datetime):
        with open(self.last_update_fpath, "wb") as fp:
            pickle.dump(dt, fp)
    
    # Returns the last time the task job's update() function was executed
    # (either failing or succeeding). The value is returned from disk. Returns
    # None if no record has been saved yet.
    def get_last_update_datetime(self):
        if not os.path.isfile(self.last_update_fpath):
            return None
        with open(self.last_update_fpath, "rb") as fp:
            return pickle.load(fp)
    
    # Uses the stored "last update datetime" to calculate the next time this
    # taskjob should be updated. If there is no saved timestamp, then
    # datetime.now() is returned.
    def get_next_update_datetime(self):
        lut = self.get_last_update_datetime()
        if lut is None:
            return datetime.now()
        return datetime.fromtimestamp(lut.timestamp() + self.refresh_rate)
    
    # Performs the same function as "get_next_update_datetime()", but instead
    # returns the number of seconds the next update time is from the given
    # datetime (which, by default, is datetime.now())
    def get_next_update_datetime_relative(self, dt=None):
        if dt is None:
            dt = datetime.now()
        return self.get_next_update_datetime().timestamp() - dt.timestamp()
    
    # Returns the name of the task job.
    def get_name(self):
        return self.__class__.__name__.lower().replace("taskjob_", "")
    
    # Returns a unique identifier for this object
    def get_id(self):
        return "%s_%s" % (self.get_name(), id(self))

    # Creates and returns the project with the given name.
    def get_project_by_name(self, todoist, name: str,
                            color="grey", parent_id=None,
                            is_favorite=False, view_style="list"):
        while True:
            try:
                proj = todoist.get_project_by_name(name)
                if proj is None:
                    proj = todoist.add_project(name, color=color, parent_id=None)
                return proj
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.log("Getting rate-limited by Todoist. Sleeping...")
                    time.sleep(self.todoist_rate_limit_timeout)
                else:
                    raise e

    # Creates and returns the section with the given name.
    def get_section_by_name(self, todoist, project_id: str, name: str,
                            order=None):
        while True:
            try:
                sect = todoist.get_section_by_name(name)
                if sect is None:
                    sect = todoist.add_section(name, project_id, order=None)
                return sect
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.log("Getting rate-limited by Todoist. Sleeping...")
                    time.sleep(self.todoist_rate_limit_timeout)
                else:
                    raise e

