# This module defines the base class for all automotive-related TaskJobs within
# the Taskmaster service. It optionally provides helpers for communicating with
# the Gearhead service via OracleSession for mileage and maintenance queries.

# Imports
import os
import sys
from datetime import datetime
import threading
import time
import inspect

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob, TaskConfig
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession, OracleSessionConfig


class TaskJob_Automotive_Config(Config):
    """Configuration for Gearhead-connected automotive TaskJobs.

    Contains connection details for the Gearhead service oracle, used by
    automotive TaskJobs that query Gearhead for mileage and due-maintenance
    data.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("gearhead", [OracleSessionConfig], required=True),
        ]


class TaskJob_Automotive(TaskJob):
    """Base class for automotive-based tasks.

    Optionally provides access to the Gearhead service for mileage and
    maintenance data. Subclasses that need Gearhead integration should call
    ``super().init()`` to load the connection config from
    ``automotive.json``.
    """
    def init(self):
        """Loads the Gearhead connection config from the automotive config
        file.

        Sets ``self.automotive_config`` to the parsed config, or ``None``
        if the config file does not exist.
        """
        config_fname = "automotive.json"
        config_fpath = os.path.join(fdir, config_fname)
        self.automotive_config = None
        if os.path.isfile(config_fpath):
            self.automotive_config = TaskJob_Automotive_Config()
            self.automotive_config.parse_file(config_fpath)

    def update(self):
        """Update function to be overridden by subclasses."""
        super().update()
        return False

    def get_project(self):
        """Returns the Todoist project for automotive tasks."""
        return self.get_project_by_name("Automotive", color="red")

    # ----------------------------- Gearhead I/O ----------------------------- #
    def get_gearhead_session(self):
        """Creates and returns an authenticated OracleSession with the
        Gearhead service oracle.

        Raises AssertionError if the Gearhead config has not been loaded.
        """
        assert self.automotive_config is not None, \
            "Gearhead config not loaded. Ensure automotive.json exists " \
            "and super().init() is called."
        s = OracleSession(self.automotive_config.gearhead)
        s.login()
        return s

    def get_current_mileage(self, gearhead_session, vehicle_id):
        """Queries Gearhead's ``GET /mileage`` endpoint and returns the
        current mileage for the given vehicle.

        Returns the mileage as a float, or ``None`` if the request fails
        or no mileage data exists.
        """
        r = gearhead_session.get("/mileage", payload={
            "vehicle_id": vehicle_id
        })
        if not OracleSession.get_response_success(r):
            self.log("Gearhead /mileage request failed: %s" %
                     OracleSession.get_response_message(r))
            return None
        payload = OracleSession.get_response_json(r)
        if not payload:
            return None
        return payload.get("mileage")

    def get_due_maintenance(self, gearhead_session, vehicle_id,
                            mileage_start, mileage_end):
        """Queries Gearhead's ``GET /maintenance/due`` endpoint with an
        explicit mileage range and returns the list of maintenance tasks
        whose thresholds fall within ``[mileage_start, mileage_end)``.

        Returns an empty list if the request fails or no maintenance is due.
        """
        r = gearhead_session.get("/maintenance/due", payload={
            "vehicle_id": vehicle_id,
            "mileage_start": mileage_start,
            "mileage_end": mileage_end
        })
        if not OracleSession.get_response_success(r):
            self.log("Gearhead /maintenance/due request failed: %s" %
                     OracleSession.get_response_message(r))
            return []
        return OracleSession.get_response_json(r)

    def find_due_maintenance(self, due_list, vehicle_id,
                             maintenance_task_id):
        """Searches a list of due maintenance items (from Gearhead) for a
        specific vehicle and maintenance task combination.

        Returns the matching due-maintenance dict, or ``None`` if not found.
        """
        for item in due_list:
            if item.get("vehicle_id") == vehicle_id and \
               item.get("task", {}).get("id") == maintenance_task_id:
                return item
        return None

