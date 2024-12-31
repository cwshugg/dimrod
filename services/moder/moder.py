#!/usr/bin/python3
# This service acts as a master "house mode" service. It defines and maintains
# the current state of the home's "mode". Some examples of "modes" might be:
#
#   - Away Mode: used when our house is empty and we want to keep it safe.
#   - Party Mode: used when having guests over.

# Imports
import os
import sys
import json
import threading
import inspect
import time
import flask
import importlib.util

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSessionConfig
from lib.cli import ServiceCLI

# Mode imports
from mode import *


# =============================== Config Class =============================== #
class ModerConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("mode_away_devices",          [list], required=True),
            ConfigField("mode_away_device_timeout",   [int],  required=False, default=1200),
            ConfigField("mode_away_address",          [str],  required=False, default=None),
            ConfigField("tick_rate",                  [int],  required=False, default=5),
            ConfigField("warden",   [OracleSessionConfig],  required=True),
            ConfigField("lumen",    [OracleSessionConfig],  required=True),
        ]


# ============================== Service Class =============================== #
class ModerService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = ModerConfig()
        self.config.parse_file(config_path)
        self.mode_list = None
        self.thread = None
        self.lock = threading.Lock()
        self.queue = [] # list/queue used to hold all possible modes
    
    # Creates and returns a list of Mode classes available.
    def get_modes(self):
        # if we've done this already return last time's result
        if self.mode_list is not None:
            return self.mode_list

        # make sure the modes directory exists
        modes_dir = os.path.join(os.path.dirname(__file__), "modes")
        assert os.path.isdir(modes_dir), "missing modes directory: %s" % modes_dir

        # search the modes directory for python files
        result = [Mode_Empty]
        for (root, dirs, files) in os.walk(modes_dir):
            for f in files:
                if f.lower().endswith(".py"):
                    # import the file
                    mpath = "modes.%s" % f.replace(".py", "")
                    mod = importlib.import_module(mpath)

                    # inspect the module's members
                    for (name, cls) in inspect.getmembers(mod, inspect.isclass):
                        # ignore the base class - append everything else that's
                        # a child of the "base" class
                        if issubclass(cls, Mode) and cls.__name__.lower() != "mode":
                            result.append(cls)

        self.mode_list = result
        return result
    
    # Launches the given service, first stopping the currently-running service
    # if needed.
    def launch(self, mode: Mode):
        # instruct the current service to *stop* (if one exists) and join it
        if self.thread is not None:
            self.log.write("Stopping mode: \"%s\"." % self.thread.mode)
            self.thread.stop()
            self.thread.join()
            # re-add a new instance of the mode to the queue (as long as it's
            # not the empty mode)
            self.enqueue(self.thread.mode.__class__(self))
            self.thread = None

        # create a new moder thread with the new mode, and fire it off
        self.thread = ModerThread(mode)
        self.log.write("Starting mode: \"%s\"." % self.thread.mode)
        self.thread.start()
    
    # Pops and returns the mode with the highest priority. If no mode with a
    # greater-than-zero priority exists, None is returned.
    def dequeue(self):
        # get the current mode's priority (if there is one). (If it's complete,
        # don't bother - we'll choose a new one without considering its
        # priority)
        current_p = 0
        if self.thread is not None and not self.thread.mode.is_complete():
            self.thread.lock.acquire()
            current_p = self.thread.mode.priority()
            self.thread.lock.release()

        self.lock.acquire()
        
        #print("QUEUE: [ ", end="")
        highest_p = current_p
        highest_i = -1
        highest_m = None
        for (i, m) in enumerate(self.queue):
            p = m.priority()
            #print("%s-%d " % (m.name, p), end="")
            if p > highest_p:
                highest_p = p
                highest_i = i
                highest_m = m
        #print("]")

        # if one was found that's greater than the current mode's priority,
        # remove it from the queue and return it
        if highest_i >= 0:
            # pop and release
            self.queue.pop(highest_i)
            self.lock.release()
            # log and return the mode
            self.log.write("Popped mode \"%s\" with priority of %d "
                           "(higher than current: %d)." %
                           (highest_m.name, highest_p, current_p))
            return (highest_m, highest_p)
        else:
            # release and return None
            self.lock.release()
            return (None, 0)
    
    # Forces a mode, regardless of its computed priority or state, to be placed
    # onto a queue to be run as soon as possible.
    def enqueue(self, mode: Mode):
        self.lock.acquire()

        # if a mode of the same type already exists, replace it with the new one
        for (i, m) in enumerate(self.queue):
            if type(m) == type(mode):
                self.queue[i] = mode
                self.lock.release()
                return
        # append and release
        self.queue.append(mode)

        self.lock.release()

    # Overridden main function implementation.
    def run(self):
        super().run()
        
        # start by filling the queue with an instance of every possible mode,
        # and by launching the first one
        for mclass in self.get_modes():
            self.enqueue(mclass(self))
        # dequeue the first and launch
        (hpm, hp) = self.dequeue()
        self.launch(hpm)

        while True:
            # pop the mode with the highest priority that's greater than the
            # current mode
            (hpm, hp) = self.dequeue()

            # if no new mode was found, there is nothing suitable to do other
            # than wait for a higher-priority mode to surface or for the current
            # mode to finish
            if hpm is None:
                time.sleep(self.config.tick_rate)
                continue

            # otherwise, we know 'hpm' references a mode with a higher priority
            # than the current one. Launch it and sleep
            self.launch(hpm)
            time.sleep(self.config.tick_rate)

# This class represents a thread spawned by the Moder service to carry out the
# currently-active house mode.
class ModerThread(threading.Thread):
    def __init__(self, mode):
        threading.Thread.__init__(self, target=self.run)
        self.lock = threading.Lock()
        self.mode = mode
        self.must_stop = False
    
    # Used to request the thread to stop, regardless of the mode's state.
    # This is used to preempt with another mode (i.e. interrupt this one and
    # start another).
    def stop(self, timeout=None):
        self.lock.acquire()
        self.must_stop = True
        self.lock.release()
    
    # The thread's main function.
    def run(self):
        # run until the mode indicates it's finished
        while not self.mode.is_complete():
            # execute the mode's 'step' function
            self.mode.step()

            # check if the thread has been commanded to stop
            self.lock.acquire()
            if self.must_stop:
                self.lock.release()
                break
            self.lock.release()
            
            # sleep for a short time
            self.mode.sleep()

        # run the cleanup routine
        self.mode.cleanup()


# ============================== Service Oracle ============================== #
class ModerOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        @self.server.route("/mode/queue", methods=["POST"])
        def endpoint_mode_set():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)
            
           # look for the optional "priority" parameter, to force a priority
            # for the desired mode
            p = None
            if "priority" in flask.g.jdata:
                # attempt to conver the priority to an integer
                try:
                    p = int(flask.g.jdata["priority"])
                except:
                    return self.make_response(msg="Invalid priority.",
                                              success=False, rstatus=400)
            
            # look for the mode name
            if "mode" not in flask.g.jdata:
                return self.make_response(msg="Missing mode name.",
                                          success=False, rstatus=400)
            mode_name = flask.g.jdata["mode"].strip().lower()
            
            # make sure the mode matches one of the known modes
            modes = self.service.get_modes()
            mode = None
            for mode_class in modes:
                m = mode_class(self.service, priority=p)
                # if the names match, save it and break
                if mode_name == m.name.strip().lower():
                    mode = m
                    break

            # if a mode wasn't found, return an error
            if mode is None:
                return self.make_response(msg="Unkonwn mode name.",
                                          success=False, rstatus=400)

            # otherwise, queue the mode for launch
            self.log.write("Received request to launch mode: \"%s\"" % mode_name)
            self.service.enqueue(mode)
            return self.make_response(success=True)

        @self.server.route("/mode/get", methods=["GET"])
        def endpoint_mode_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            
            # build a payload and respond with the mode name
            pyld = {"mode": self.service.thread.mode.name}
            return self.make_response(success=True, payload=pyld)

        @self.server.route("/mode/get_all", methods=["GET"])
        def endpoint_mode_get_all():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            
            # respond with all mode names
            pyld = []
            for mclass in self.service.get_modes():
                pyld.append(mclass(self.service).name)
            return self.make_response(success=True, payload=pyld)


        
# =============================== Runner Code ================================ #
cli = ServiceCLI(config=ModerConfig, service=ModerService, oracle=ModerOracle)
cli.run()

