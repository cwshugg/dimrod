# This module implements basic communication with the TickTick API.

# Imports
import os
import sys
import json
import requests
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Globals
ticktick_url_base = "https://api.ticktick.com/api/v2"


# ============================== TickTick Tasks ============================== #
# Used to parse individual tasks from TickTick's API.
class TickTickTaskConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",               [str],      required=True),
            ConfigField("title",            [str],      required=True),
            ConfigField("projectId",        [str],      required=True),
            ConfigField("content",          [str],      required=False,     default=""),
            ConfigField("sortOrder",        [int],      required=False,     default=0),
        ]

# Represents a single TickTick task.
class TickTickTask:
    # Constructor.
    def __init__(self, tid: str, title: str, content="", pid="", sort_order=0):
        self.tid = tid
        self.title = title
        self.content = content
        self.pid = pid
        self.sort_order = sort_order

    # String representation
    def __str__(self):
        msg = "[T-%s] %s" % (self.tid, self.title)
        if len(self.content) > 0:
            msg += ": %s" % self.content
        return msg

    # --------------------------- JSON Conversion ---------------------------- #
    # Creates a task object from a given JSON dictionary.
    @staticmethod
    def from_json(jdata: dict):
        conf = TickTickTaskConfig()
        conf.parse_json(jdata)
        return TickTickTask(conf.id, conf.title,
                            content=conf.content,
                            pid=conf.projectId,
                            sort_order=conf.sortOrder)

    # Returns a JSON representation of the task.
    def to_json(self):
        result = {
            "tid": self.tid,
            "title": self.title,
            "content": self.content,
            "pid": "UNKNOWN" if not hasattr(self, "pid") else self.pid,
            "sort_order": 0 if not hasattr(self, "sort_order") else self.sort_order
        }
        return result


# ============================ TickTick Projects ============================= #
# Used to parse individual projects from TickTick's API.
class TickTickProjectConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",           [str],      required=True),
            ConfigField("name",         [str],      required=True)
        ]

# Represents a single TickTick project.
class TickTickProject:
    # Constructor. Takes in the project's ID and its name.
    def __init__(self, pid: str, name: str, tasks=[]):
        self.pid = pid
        self.name = name
        self.tasks = tasks if len(tasks) > 0 else []
    
    # String representation
    def __str__(self):
        return "[P-%s] %s: %d tasks" % (self.pid, self.name, len(self.tasks))
    
    # Adds a new task to the project. Maintains sorted order.
    def add(self, task: TickTickTask):
        # if the task doesn't have a sorted order, we'll generate one based on
        # the value at the end of the list
        if not hasattr(task, "sort_order") or task.sort_order == 0:
            task.sort_order = 0 if len(self.tasks) == 0 else self.tasks[-1].sort_order
            
        # find the correct spot to insert
        i = 0
        for i in range(len(self.tasks)):
            if self.tasks[i].sort_order > task.sort_order:
                break
        self.tasks.insert(0, task)
    
    # --------------------------- JSON Conversion ---------------------------- #
    # Creates an empty project object from JSON data.
    @staticmethod
    def from_json(jdata: dict):
        conf = TickTickProjectConfig()
        conf.parse_json(jdata)
        return TickTickProject(conf.id, conf.name, [])
    
    # Returns a JSON representation of the project object.
    def to_json(self):
        result = {"pid": self.pid, "name": self.name, "tasks": []}
        for t in self.tasks:
            result["tasks"].append(t.to_json())
        return result


# ============================== Main API Class ============================== #
# Main API class.
class TickTickAPI:
    # Constructor.
    def __init__(self, username: str, password: str, refresh_threshold=60):
        self.auth_username = username
        self.auth_password = password
        self.session = None
        
        # this API will only refresh after a certain amount of time has passed
        # (in order to make responses faster)
        self.refresh_threshold = refresh_threshold
        self.last_refresh = datetime.fromtimestamp(0)
    
    # Generate request-sending helper function.
    def send(self, method: str, endpoint: str, params={}, headers={}, payload=None):
        endpoint = endpoint[1:] if endpoint.startswith("/") else endpoint
        url = ticktick_url_base + "/" + endpoint

        # ticktick doesn't seem to like the 'python-requests' user agent, so
        # we'll override that here
        headers["User-Agent"] = "DImROD"
        
        # if we were given a payload, add the content-type header
        if payload is not None:
            headers["Content-Type"] = "application/json"
        
        # parse the method and send the request accordingly
        method = method.strip().lower()
        if method == "get":
            return self.session.get(url, params=params, headers=headers)
        elif method == "post":
            return self.session.post(url, params=params, headers=headers,
                                     json=payload)
        
        assert False, "unsupported HTTP method: \"%s\"" % method

    # Gets a new session, attempts to log in, and attempts to pull down the
    # current user data. The user data is returned.
    def refresh(self):
        # close the existing session
        if self.session is not None:
            self.session.close()

        # create a new session and attempt to log into ticktick
        self.session = requests.Session()
        params = {"wc": True, "remember": True}
        login_data = {
            "username": self.auth_username,
            "password": self.auth_password
        }

        # send the request
        r = self.send("POST", "/user/signon", params=params, payload=login_data)
        assert r.status_code == 200, "failed to sign into TickTick (%d) %s" % \
                                     (r.status_code, r.text)

        # retrieve all current tasks/projects
        self.retrieve()

        # reset the last-refreshed field
        self.last_refresh = datetime.now()

    # Retrieves all tasks and projects.
    def retrieve(self):
        # next, we'll pull down all of the user's data and organize them into
        # class objects
        r = self.send("GET", "/batch/check/0")
        assert r.status_code == 200, "failed to retrieve TickTick data (%d) %s" % \
                                     (r.status_code, r.text)
        jdata = r.json()

        # retrieve all projects from the JSON payload
        projects = {}
        pdata = jdata["projectProfiles"]
        for p in pdata:
            pconf = TickTickProjectConfig()
            pconf.parse_json(p)
            proj = TickTickProject(pconf.id, pconf.name)
            projects[proj.pid] = proj
        
        # retrieve all tasks from the JSON payload
        tasks = []
        tdata = jdata["syncTaskBean"]["update"]
        for t in tdata:
            tconf = TickTickTaskConfig()
            tconf.parse_json(t)
            task = TickTickTask(tconf.id, tconf.title,
                                content=tconf.content,
                                pid=tconf.projectId,
                                sort_order=tconf.sortOrder)

            # add to the appropriate project
            if task.pid not in projects:
                unknown_key = "UNKNOWN"
                task.pid = unknown_key
                if unknown_key not in projects:
                    projects[task.pid] = TickTickProject(unknown_key, unknown_key)

            projects[task.pid].add(task)

        # save the projects to a class field
        self.projects = projects
        return projects
    
    # Determines if we're past the refresh threshold. Calls 'self.refresh' if
    # so, otherwise skips the refresh. Either way, the current list of projects
    # is returned.
    def maybe_refresh(self):
        now_ts = datetime.now().timestamp()
        last_ts = self.last_refresh.timestamp()
        if now_ts - last_ts >= self.refresh_threshold:
            return self.refresh()
        return self.projects


    # ============================== Retrieval =============================== #
    # Gets all projects and returns them as a list of objects.
    def get_projects(self):
        self.maybe_refresh()
        return self.projects
    
    # Retrieves a single project ID and returns it.
    def get_project(self, pid: str):
        self.maybe_refresh()
        if pid not in self.projects:
            return None
        return self.projects[pid]

