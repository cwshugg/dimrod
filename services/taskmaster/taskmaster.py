#!/usr/bin/python3
# The Taskmaster is the ONLY service allowed access to the internet. It's job is
# to receive commands from my internet-connected devices and issue commands to
# the other services.

# Imports
import os
import sys
import json
import flask
import asyncio
import threading
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Service imports
from event import TaskmasterEvent, TaskmasterEventPostConfig


# =============================== Config Class =============================== #
class TaskmasterConfig(ServiceConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("taskmaster_events",        [list], required=True),
            ConfigField("taskmaster_thread_limit",  [int],  required=False, default=8)
        ]


# ============================== Service Class =============================== #
class TaskmasterService(Service):
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = TaskmasterConfig()
        self.config.parse_file(config_path)
        self.threads = []

        # parse each event as an event object
        self.events = []
        for edata in self.config.taskmaster_events:
            e = TaskmasterEvent(edata)
            self.events.append(e)
            self.log.write("Loaded event: %s" % str(e))

    # Overridden abstract class implementation for the service thread.
    def run(self):
        super().run()

    # Accepts a TaskmasterEventPostConfig and searches the service's events for one
    # with a matching name. Returns the number of events that were fired as a
    # result.
    def post(self, pconf: TaskmasterEventPostConfig):
        # Helper function that fires a single event and all of its subscribers.
        def run_event(e: TaskmasterEvent, out_fd, err_fd):
            asyncio.run(e.fire(data=pconf.data,
                                stdout_fd=out_fd,
                                stderr_fd=err_fd))
            self.log.return_fd(out_fd)
        
        # iterate through all events and fire off those that match
        matches = 0
        for e in self.events:
            # if the name matches the event, we'll fire it (and pass along any
            # extra data)
            if e.config.name == pconf.name:
                # set up two file descriptors: (STDOUT and STDERR)
                out_fd = self.log.rent_fd()
                err_fd = out_fd

                # make sure we aren't maxed out on threads. If we are, we'll
                # have to wait for one to complete before we spawn another
                if len(self.threads) >= self.config.taskmaster_thread_limit:
                    self.log.write("Joining old thread...")
                    dt1 = datetime.now()
                    old_thread = self.threads.pop(0)
                    old_thread.join()

                    # measure the time and report how long it took
                    dt2 = datetime.now()
                    timediff = dt2.timestamp() - dt1.timestamp()
                    self.log.write("Joined thread after %d seconds." % timediff)

                # otherwise, create the new thread, append it to our thread
                # queue, then start it
                self.log.write("Firing event: %s" % e.config.name)
                t = threading.Thread(target=run_event, args=[e, out_fd, err_fd])
                self.threads.append(t)
                t.start()
                matches += 1
        return matches


# ============================== Service Oracle ============================== #
class TaskmasterOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()
        
        # Endpoint for a simple greeting.
        @self.server.route("/events/get", methods=["GET"])
        def endpoint_events_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # convert all events into JSON and return it in the response
            events = []
            for e in self.service.events:
                events.append(e.config.to_json())
            return self.make_response(success=True, payload=events)

        # Endpoint for a goodbye.
        @self.server.route("/events/post", methods=["POST"])
        def endpoint_events_post():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="No JSON data provided.",
                                          success=False)
            
            # interpret the data as a taskmaster event post config object to ensure
            # all the correct fields were given
            pconf = TaskmasterEventPostConfig()
            try:
                pconf.parse_json(flask.g.jdata)
            except Exception:
                return self.make_response(msg="Invalid JSON data.",
                                          success=False)

            # next, pass the event name and the data to the service to find the
            # correct service and run it
            matches = self.service.post(pconf)

            # depending on how many events were fired as a result, we'll return
            # a match
            if matches == 0:
                return self.make_response(msg="Unknown event: \"%s\"" % pconf.name,
                                          success=False)
            return self.make_response(msg="Event successfully posted.")
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=TaskmasterConfig, service=TaskmasterService, oracle=TaskmasterOracle)
cli.run()

