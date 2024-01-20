#!/usr/bin/python3
# The taskmaster is responsible for adding important chores and tasks to my
# Todoist todo list.

# Imports
import os
import sys
import json
import flask
from datetime import datetime
import inspect
import importlib.util
import time

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(fdir)
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI
from lib.todoist import Todoist

# Service imports
from task import TaskJob

task_directory = os.path.join(fdir, "tasks")


# =============================== Config Class =============================== #
class TaskmasterConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("taskmaster_todoist_api_key",   [str], required=True),
            ConfigField("taskmaster_refresh_rate",      [int], required=False,  default=5400),
            ConfigField("openai_api_key",               [str], required=True)
        ]


# ============================== Service Class =============================== #
class TaskmasterService(Service):
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = TaskmasterConfig()
        self.config.parse_file(config_path)
        self.task_dict = {}
    
    # Imports all task jobs in the task job directory.
    def get_jobs(self):
        assert os.path.isdir(task_directory), "missing task directory: %s" % task_directory

        # search the task directory for python files
        self.task_dict = {}
        for (root, dirs, files) in os.walk(task_directory):
            for f in files:
                if f.lower().endswith(".py"):
                    # import the file
                    mpath = "%s.%s" % (os.path.basename(task_directory), f.replace(".py", ""))
                    mod = importlib.import_module(mpath)

                    # inspect the module's members
                    for (name, cls) in inspect.getmembers(mod, inspect.isclass):
                        # ignore the base class - append everything else that's
                        # a child of the "base" class
                        if issubclass(cls, TaskJob) and cls.__name__.lower() != "taskjob":
                            # use the unique name of the taskjob as an index
                            # into the dictionary to keep one of each at most
                            name = cls(self).get_name()
                            if name not in self.task_dict:
                                self.task_dict[name] = cls

        return self.task_dict

    # Overridden abstract class implementation for the service thread.
    def run(self):
        super().run()

        # retrieve a Todoist API instance
        todoist = Todoist(self.config.taskmaster_todoist_api_key)
       
        taskjobs_len = 0
        while True:
            # import all task job subclasses and log a message when the number
            # of loaded job classes has changed
            taskjobs = self.get_jobs()
            taskjobs_len_new = len(taskjobs)
            if taskjobs_len_new != taskjobs_len:
                self.log.write("Loaded %d taskjobs (%s from %d)." %
                               (taskjobs_len_new,
                                "up" if taskjobs_len_new > taskjobs_len else "down",
                                taskjobs_len))
                taskjobs_len = taskjobs_len_new

            # for each task job, initialize a class object and call it's update
            # function
            for name in taskjobs:
                tj = taskjobs[name]
                try:
                    j = tj(self)
                    is_success = j.update(todoist)

                    # if the task succeeded in updating Todoist, save a record of
                    # the datetime for the task to reference later, if needed
                    if is_success:
                        now = datetime.now()
                        j.set_last_success_datetime(now)
                        self.log.write("Task \"%s\" succeeded at %s." %
                                       (j.get_name(),
                                        now.strftime("%Y-%m-%d %I:%M:%S %p")))
                except Exception as e:
                    self.log.write("Task \"%s\" failed to execute: %s" %
                                   (j.get_name(), e))

            time.sleep(self.config.taskmaster_refresh_rate)

# ============================== Service Oracle ============================== #
class TaskmasterOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # TODO
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=TaskmasterConfig, service=TaskmasterService, oracle=TaskmasterOracle)
cli.run()

