# This module provides a basic wrapper around the official Todoist Python SDK.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Todoist imports
from todoist_api_python.api import TodoistAPI

class Todoist:
    # Constructor. Takes in a Todoist API key.
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_obj = None

        # these fields determine when to use the cached list of projects and
        # when to refresh
        self.projects = []
        self.projects_last_dt = None # timestamp of last retrieval
        self.projects_refresh_rate = 15 # number of seconds before reloading projects
    
        # these work the same, but for section retrieval
        self.sections = []
        self.sections_last_dt = None # timestamp of last retrieval
        self.sections_refresh_rate = 15 # number of seconds before reloading sections
    
        # these work the same, but for task retreival
        self.tasks = []
        self.tasks_last_dt = None # timestamp of last retrieval
        self.tasks_refresh_rate = 15 # number of seconds before reloading tasks
        self.tasks_refresh_force = False # switch used to force a refresh
    
    # Initializes the class' API instance (if it's not yet initialized). The
    # API object is returned.
    def api(self):
        if self.api_obj is None:
            self.api_obj = TodoistAPI(self.api_key)
        return self.api_obj
    
    # ------------------------------- Projects ------------------------------- #
    # Returns a list of all projects. The API may be called, or the cached list
    # may be used, depending on when the last call was. If 'refresh' is True,
    # the API will be called regardless.
    def get_projects(self, refresh=False):
        # refresh, if applicable
        now = datetime.now()
        if self.projects_last_dt is None or refresh or \
           now.timestamp() - self.projects_last_dt.timestamp() > self.projects_refresh_rate:
            # ping the API for a list of projects
            api = self.api()
            self.projects = api.get_projects()
            self.projects_last_dt = now

        return self.projects
    
    # Searches for a project with the given ID, returning it if found.
    def get_project_by_id(self, project_id: str):
        # get a list of projects and search for the project ID
        projs = self.get_projects()
        for proj in projs:
            if proj.id == proj_id:
                return proj
        return None

    # Searches the list of projects for a project with the given name. Returns
    # the project object or None.
    def get_project_by_name(self, name: str):
        projs = self.get_projects()
        for proj in projs:
            if proj.name == name:
                return proj
        
        # if we got here, the project isn't in the current list
        return None
            
    # Creates a new project via the Todoist API.
    def add_project(self, name: str, parent_id=None, color="grey",
                    is_favorite=False, view_style="list"):
        api = self.api()
        proj = api.add_project(name=name, parent_id=parent_id, color=color,
                               is_favorite=is_favorite, view_style=view_style)
        # update the cached list of projects to include the new one
        self.projects.append(proj)
        return proj

    # ------------------------------- Sections ------------------------------- #
    # Returns a list of all sections. This works the same was as get_projects()
    # in regard to caching and refreshing. If 'project_id' is specified, only
    # sections for that project will be returned. Returns a list - (empty if
    # there are no sections).
    def get_sections(self, refresh=False, project_id=None):
        # refresh, if applicable
        now = datetime.now()
        if self.sections_last_dt is None or refresh or \
           now.timestamp() - self.sections_last_dt.timestamp() > self.sections_refresh_rate:
            # ping the API for a list of sections
            api = self.api()
            self.sections = api.get_sections()
            self.sections_last_dt = now

        # if a specific project was specified, search through the list and
        # return only those that match the project
        if project_id is not None:
            result = []
            for sect in self.sections:
                if sect.project_id == project_id:
                    result.append(sect)
            return result
        # otherwise, return all sections
        return self.sections
    
    # Searches for a section with the given ID, returning it if found.
    def get_section_by_id(self, section_id: str):
        # get a list of sections and search for the section ID
        sects = self.get_sections()
        for sect in sects:
            if sect.id == section_id:
                return sect
        return None

    # Returns a section matching the given name, or None. If 'project_id' is
    # specified, only sections belonging to that project will be examined.
    def get_section_by_name(self, name: str, project_id=None):
        sects = self.get_sections()
        for sect in sects:
            # if a project ID was given, make sure this section belongs to it
            if project_id is not None and sect.project_id != project_id:
                continue
            # check for a name match
            if sect.name == name:
                return sect
    
    # Creates a new section via the API, given a name and project ID (and other
    # optional fields).
    def add_section(self, name: str, project_id: str, order=None):
        api = self.api()
        sect = api.add_section(name=name, project_id=project_id, order=order)
        # update the cached list of sections
        self.sections.append(sect)
        return sect
    
    # -------------------------------- Tasks --------------------------------- #
    # Returns a list of all active Todoist tasks. This works the same as
    # get_projects() in terms of caching and refreshing.
    # If 'project_id' is specified, only tasks belonging to that particular
    # project will be returned.
    # If 'section_id' is specified, only tasks belonging to that particular
    # section will be returned.
    def get_tasks(self, refresh=False, project_id=None, section_id=None):
        # if the force flag is set, toggle 'refresh' and reset the flag
        if self.tasks_refresh_force:
            refresh = True
            self.tasks_refresh_force = False

        # refresh, if applicable
        now = datetime.now()
        if self.tasks_last_dt is None or refresh or \
           now.timestamp() - self.tasks_last_dt.timestamp() > self.tasks_refresh_rate:
            # ping the API for a list of tasks
            api = self.api()
            self.tasks = api.get_tasks()
            self.tasks_last_dt = now

        # iterate through the tasks and build up a list containing only the
        # filtered tasks (if any filters were specified)
        result = []
        for task in self.tasks:
            # if a project ID was specified and this one doesn't match, skip it
            if project_id is not None and task.project_id != project_id:
                continue
            # if a section ID was specified and this one doesn't match, skip it
            if section_id is not None and task.section_id != section_id:
                continue
            result.append(task)
        return result
    
    # Searches for a task with the given ID, returning it if found.
    def get_task_by_id(self, task_id: str):
        # get a list of tasks and search for the task ID
        tasks = self.get_tasks()
        for task in tasks:
            if task.id == task_id:
                return task
        return None

    # Searches the list of tasks and looks for tasks with the given name.
    # If 'project_id' is specified, only tasks belonging to that particular
    # project will be searched.
    # If 'section_id' is specified, only tasks belonging to that particular
    # section will be searched.
    # Returns None if a match wasn't found.
    def get_task_by_title(self, title: str, project_id=None, section_id=None):
        tasks = self.get_tasks(project_id=project_id, section_id=section_id)
        for task in tasks:
            # if the title matches, return it
            if task.content == title:
                return task

        # if we got here, the task isn't in the current list
        return None
    
    # Adds a task to Todoist, given the title string, body string, and other
    # optional information.
    def add_task(self, title: str, body: str,
                 project_id=None, section_id=None, due_datetime=None,
                 priority=1, labels=[]):
        api = self.api()
        
        # make the API call
        due_dt = None if due_datetime is None else due_datetime.isoformat()
        task = api.add_task(content=title,
                            description=body,
                            project_id=project_id,
                            section_id=section_id,
                            due_datetime=due_dt,
                            priority=priority,
                            labels=labels)
        # update the cached list of tasks
        self.tasks.append(task)
        return task
    
    # Deletes the task specified by the task ID.
    def delete_task(self, task_id: str):
        api = self.api()
        api.delete_task(task_id=task_id)
        
        # delete the local copy of this task
        for (i, t) in enumerate(self.tasks):
            if t.id == task_id:
                self.tasks.pop(i)
                break
        return True
    
    # Updates an existing task with any non-None fields. Returns None if a task
    # with the given ID is not found. Otherwise, the updated task is returned.
    def update_task(self, task_id: str, title=None, body=None, labels=None,
                    priority=None, due_datetime=None):
        t = self.get_task_by_id(task_id)
        if t is None:
            return None
        
        # choose an appropriate due datetime
        due_dt = None
        if due_datetime is not None:
            due_dt = due_datetime.isoformat()
        elif t.due is not None:
            due_dt = t.due.datetime

        # issue an API request to update the task
        api = self.api()
        task = api.update_task(task_id=task_id,
                               content=t.content if title is None else title,
                               description=t.description if body is None else body,
                               labels=t.labels if labels is None else labels,
                               priority=t.priority if priority is None else priority,
                               due_datetime=due_dt)
        
        # now that a task is updated on the remote end, we must refresh the
        # next time tasks are retrieved, no matter how long it's been.
        # Otherwise we'll be working with stale data. So, flip a flag that
        # requires a refresh next time
        self.tasks_refresh_force = True
        return task
    
    # Deletes the given task and creates a copy in the new project and/or
    # section. Does nothing if neither a project ID or section ID is specified.
    # Returns the ID of the new task (or the ID of the old task if nothing was
    # accomplished.)
    # This delete+recreate process must be done, because Todoist unfortunately
    # has no documented way to move a task in its API.
    def move_task(self, task_id: str, project_id=None, section_id=None):
        if project_id is None and section_id is None:
            return task_id
        
        # retrieve the task and delete it
        task = self.get_task_by_id(task_id)
        self.delete_task(task.id)

        # re-create the task with the same information as the original, with
        # the new project/section IDs
        api = self.api()
        t = api.add_task(content=task.content,
                         description=task.description,
                         project_id=task.project_id if project_id is None else project_id,
                         section_id=task.section_id if section_id is None else section_id,
                         parent_id=task.parent_id,
                         order=task.order,
                         labels=task.labels,
                         priority=task.priority,
                         due_datetime=None if task.due is None else task.due.datetime,
                         assignee_id=None if not hasattr(task, "assignee_id") else task.assignee_id,
                         duration=None if not hasattr(task, "duration") else task.duration,
                         duration_unit=None if not hasattr(task, "duration_unit") else task.duration_unit)
        return t.id
    
