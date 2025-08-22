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
import traceback
import threading

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
from lib.google.google_calendar import GoogleCalendar, GoogleCalendarConfig
from lib.oracle import OracleSession, OracleSessionConfig
from lib.dialogue.dialogue import DialogueConfig

# Service imports
from task import TaskJob

task_directory = os.path.join(fdir, "tasks")


# ============================== Multithreading ============================== #
# An object that allows the queue-user to wait on specific queued jobs to be
# complete.
class TaskmasterThreadQueueFuture:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)
        self.is_complete = False

    def wait(self):
        self.lock.acquire()
        while not self.is_complete:
            self.cond.wait()
        self.lock.release()

    def mark_complete(self):
        self.lock.acquire()
        self.is_complete = True
        self.cond.notify()
        self.lock.release()

# An object that is submitted to the thread queue.
class TaskmasterThreadQueueEntry:
    def __init__(self, taskjob: TaskJob):
        self.taskjob = taskjob
        self.future = TaskmasterThreadQueueFuture()

# A queue used to submit taskjobs to taskmaster worker threads.
class TaskmasterThreadQueue:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)
        self.queue = []

    # Pushes to the queue and alerts a waiting thread.
    def push(self, taskjob: TaskJob):
        # put together an entry object to submit to the queue
        entry = TaskmasterThreadQueueEntry(taskjob)

        self.lock.acquire()
        self.queue.append(entry)
        self.cond.notify()
        self.lock.release()

        # return the future
        return entry.future
    
    # Pops from the queue, blocking if the queue is empty.
    def pop(self):
        self.lock.acquire()
        while len(self.queue) == 0:
            self.cond.wait()
        entry = self.queue.pop(0)
        self.lock.release()
        return entry

# Represents an individual thread used to handle the running of taskjobs.
# Because some taskjob updates may have a noticeable latency, these threads
# provide a way to parallelize things.
class TaskmasterThread(threading.Thread):
    def __init__(self, service, queue: TaskmasterThreadQueue):
        super().__init__(target=self.run)
        self.service = service
        self.queue = queue

    # Writes a log message using the lumen service's log object.
    def log(self, msg: str):
        ct = threading.current_thread()
        self.service.log.write("[Worker Thread %d] %s" % (ct.native_id, msg))
    
    # The thread's main function.
    def run(self):
        self.log("Spawned.")

        # loop forever
        while True:
            # pop from the queue (this will block if the queue is empty)
            entry = self.queue.pop()
            j = entry.taskjob
            f = entry.future

            # attempt to execute the queued taskjob
            try:
                is_success = j.update(self.service.get_todoist(),
                                      self.service.get_gcal())
                now = datetime.now()
                j.set_last_update_datetime(now)
    
                # if the task succeeded, save the timestamp to disk
                if is_success:
                    j.set_last_success_datetime(now)
                    self.log("Task \"%s\" succeeded at %s." %
                             (j.get_name(), now.strftime("%Y-%m-%d %I:%M:%S %p")))
            except Exception as e:
                self.log("Task \"%s\" failed to execute: %s" %
                         (j.get_name(), e))
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log(line)

            # whether it failed or succeeded, we want to unblock the main
            # thread (which is waiting on this future), so mark the taskjob's
            # future as completed
            f.mark_complete()
            

# ========================= Taskmaster Main Objects ========================== #
class TaskmasterConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("taskmaster_todoist_api_key",   [str], required=True),
            ConfigField("google_calendar",              [GoogleCalendarConfig], required=True),
            ConfigField("refresh_rate",     [int], required=False, default=300),
            ConfigField("worker_threads",   [int], required=False, default=8),
            ConfigField("lumen",            [OracleSessionConfig], required=True),
            ConfigField("telegram",         [OracleSessionConfig], required=True),
            ConfigField("speaker",          [OracleSessionConfig], required=True),
            ConfigField("dialogue",         [DialogueConfig],      required=True),
        ]

class TaskmasterService(Service):
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = TaskmasterConfig()
        self.config.parse_file(config_path)
        self.task_dict = {}

        # set up a threading queue and spawn worker threads
        self.queue = TaskmasterThreadQueue()
        self.threads = []
        for i in range(self.config.worker_threads):
            t = TaskmasterThread(self, self.queue)
            t.start()
            self.threads.append(t)
    
    # Imports all task jobs in the task job directory.
    def get_jobs(self):
        assert os.path.isdir(task_directory), "missing task directory: %s" % task_directory

        # search the task directory for python files
        self.task_dict = {}
        for (root, dirs, files) in os.walk(task_directory):
            for f in files:
                if f.lower().endswith(".py"):
                    # create a string that python will recognize as an
                    # importable module path
                    mpath_full = os.path.join(root, f)
                    mpath = mpath_full.replace(os.path.realpath(task_directory), "").strip("/")
                    mpath = mpath.replace(".py", "")
                    mpath = mpath.replace("/", ".")
                    mpath = "%s.%s" % (os.path.basename(task_directory), mpath)

                    # import the module
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
    
    # Uses the lumen configuration fields to retrieve and return an
    # authenticated Lumen OracleSession.
    def get_lumen_session(self):
        ls = OracleSession(self.config.lumen)
        ls.login()
        return ls

    # Creates and returns a new OracleSession with the speaker.
    # If authentication fails, None is returned.
    def get_speaker_session(self):
        s = OracleSession(self.config.speaker)
        r = s.login()
        if not OracleSession.get_response_success(r):
            self.log.write("Failed to authenticate with speaker: %s" %
                           OracleSession.get_response_message(r))
            return None
        return s
    
    # Performs a oneshot LLM call and response.
    def dialogue_oneshot(self, intro: str, message: str):
        # attempt to connect to the speaker
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return message

        # ping the /oneshot endpoint
        pyld = {"intro": intro, "message": message}
        r = speaker.post("/oneshot", payload=pyld)
        if OracleSession.get_response_success(r):
            # extract the response and return the reworded message
            rdata = OracleSession.get_response_json(r)
            return str(rdata["message"])
        
        # if the above didn't work, just return the original message
        self.log.write("Failed to get a oneshot response from speaker: %s" %
                       OracleSession.get_response_message(r))
        return message
    
    # Returns a Todoist API object.
    def get_todoist(self):
        return Todoist(self.config.taskmaster_todoist_api_key)
    
    # Returns a Google Calendar API object.
    def get_gcal(self):
        return GoogleCalendar(self.config.google_calendar)

    # Overridden abstract class implementation for the service thread.
    def run(self):
        super().run()

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

            # for each task job, initialize a class object and determine if
            # it's time to update
            closest_update_time_seconds = None
            taskjob_launch_data = []
            for name in taskjobs:
                # get the taskjob class and create an object
                tj_class = taskjobs[name]
                tj = tj_class(self)

                # compute the amount of time, from now, that this taskjob
                # needs to wait before updating again
                now = datetime.now()
                seconds_until_update = tj.get_next_update_datetime_relative(now)

                # if it's time to update the taskjob, we'll submit it to the
                # queue for processing by the worker threads
                if seconds_until_update < 1:
                    future = self.queue.push(tj)
                    ld = {
                        "taskjob": tj,
                        "future": future
                    }
                    taskjob_launch_data.append(ld)
                # if it's NOT time to update the taskjob, update the closest
                # update time, if this one is coming up the soonest
                elif closest_update_time_seconds is None or \
                     seconds_until_update < closest_update_time_seconds:
                    closest_update_time_seconds = seconds_until_update

            # iterate through all of the taskjobs that were just processed, and
            # wait for them to complete
            for ld in taskjob_launch_data:
                future = ld["future"]
                future.wait()
            
            # iterate through them *again* (now that they're all finished), and
            # recalculate how much time they'll need to update next
            for ld in taskjob_launch_data:
                tj = ld["taskjob"]
                now = datetime.now()
                seconds_until_update = tj.get_next_update_datetime_relative(now)

                # update the closest update time, if applicable
                if closest_update_time_seconds is None or \
                   seconds_until_update < closest_update_time_seconds:
                    closest_update_time_seconds = seconds_until_update
            
            # find the minimum amount of time (if *no* taskjobs were updated
            # above, default to some time)
            if closest_update_time_seconds is None:
                closest_update_time_seconds = self.config.refresh_rate
            time.sleep(closest_update_time_seconds)

# ============================== Service Oracle ============================== #
class TaskmasterOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # TODO
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=TaskmasterConfig, service=TaskmasterService, oracle=TaskmasterOracle)
cli.run()

