# This module defines the base class for routine mileage-based automotive
# maintenance TaskJobs. Instead of using hardcoded time-based scheduling, it
# queries the Gearhead service's ``GET /maintenance/due`` endpoint to determine
# when maintenance is due based on actual vehicle mileage.

# Imports
import os
import sys
import pickle
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig
from tasks.automotive.base import *
import lib.dtu as dtu


class TaskJob_Automotive_Routine_Maintenance(TaskJob_Automotive):
    """Base class for routine automotive maintenance tasks driven by Gearhead.

    Queries the Gearhead service to determine whether a specific mileage-based
    maintenance task is due for a given vehicle. When a new mileage threshold
    is reached (i.e. the ``triggered_mileage`` returned by Gearhead differs
    from the last-processed value), creates or updates the corresponding
    Todoist task.

    Subclasses must set the following attributes in their ``init()`` method:

    - ``self.vehicle_id``           — Gearhead vehicle ID (e.g. ``"elantra"``)
    - ``self.maintenance_task_id``  — Gearhead MaintenanceTask ID
                                      (e.g. ``"routine_5k"``)
    - ``self.title``                — Todoist task title
    - ``self.content``              — path to the Todoist task content file
    """
    def init(self):
        """Initialization hook.

        Calls the parent ``init()`` to load the Gearhead config, then sets
        default attribute values and configures the path for persisting the
        last-processed triggered mileage.
        """
        super().init()
        self.vehicle_id = None
        self.maintenance_task_id = None
        self.title = "Routine Car Maintenance"
        self.content = None

        # path to persist the last triggered mileage on disk (prevents
        # duplicate Todoist tasks when the same threshold is still "due")
        self._last_triggered_mileage_fpath = os.path.join(
            fdir, ".%s_last_triggered_mileage.pkl" %
            self.__class__.__name__.lower()
        )

    def update(self):
        """Queries Gearhead for due maintenance and creates/updates a Todoist
        task if a new mileage threshold has been reached.

        Fetches the vehicle's current mileage, then queries for maintenance
        tasks with thresholds in ``[0, current_mileage + 1)`` — effectively
        finding all thresholds up to and including the current mileage.

        Returns ``True`` if a Todoist task was created or updated; ``False``
        otherwise.
        """
        # guard: subclasses must set vehicle_id and maintenance_task_id
        if self.vehicle_id is None or self.maintenance_task_id is None:
            return False

        # guard: Gearhead config must be available
        if self.automotive_config is None:
            self.log("No Gearhead config available. Skipping.")
            return False

        # query Gearhead for the vehicle's current mileage
        try:
            gearhead = self.get_gearhead_session()
            current_mileage = self.get_current_mileage(
                gearhead, self.vehicle_id
            )
        except Exception as e:
            self.log("Failed to get mileage from Gearhead: %s" % e)
            return False

        if current_mileage is None:
            self.log("No mileage data for vehicle '%s'. Skipping." %
                     self.vehicle_id)
            return False

        # query Gearhead for due maintenance in [0, current_mileage + 1)
        # — this captures all thresholds up to and including current mileage
        try:
            due_list = self.get_due_maintenance(
                gearhead, self.vehicle_id, 0, current_mileage + 1
            )
        except Exception as e:
            self.log("Failed to query Gearhead: %s" % e)
            return False

        # find the matching maintenance task for this TaskJob
        due_item = self.find_due_maintenance(
            due_list, self.vehicle_id, self.maintenance_task_id
        )
        if due_item is None:
            self.log("Maintenance task '%s' for vehicle '%s' is not due." %
                     (self.maintenance_task_id, self.vehicle_id))
            return False

        # use the highest triggered mileage for dedup (matches old behavior)
        triggered_mileage = max(due_item.get("triggered_mileages"))

        # check if we've already processed this triggered mileage
        last_triggered = self._get_last_triggered_mileage()
        if last_triggered is not None and \
           triggered_mileage == last_triggered:
            self.log("Already processed triggered mileage %.0f for "
                     "'%s'. Skipping." %
                     (triggered_mileage, self.maintenance_task_id))
            return False

        # maintenance is due at a new threshold — create/update Todoist task
        todoist = self.get_todoist()
        proj = self.get_project()
        sect = self.get_section_by_name(proj.id, "Upkeep")

        t = TaskConfig()
        t.parse_json({
            "title": self.title,
            "content": self.content
        })

        # select a due date: ~15 days from now
        now = datetime.now()
        due = dtu.add_days(now, 15)
        due = dtu.set_time_end_of_day(due)

        # retrieve existing task or create a new one
        task = todoist.get_task_by_title(
            t.title, project_id=proj.id, section_id=sect.id
        )
        if task is None:
            todoist.add_task(
                t.title, t.get_content(),
                project_id=proj.id, section_id=sect.id,
                due_datetime=due, priority=t.priority,
                labels=t.labels
            )
        else:
            todoist.update_task(task.id, body=t.get_content(),
                                due_datetime=due)

        # persist the triggered mileage to prevent duplicate Todoist tasks
        self._set_last_triggered_mileage(triggered_mileage)

        self.log("Maintenance '%s' due for '%s' at %.0f mi "
                 "(triggered at %.0f mi). Todoist task "
                 "created/updated." %
                 (self.maintenance_task_id, self.vehicle_id,
                  current_mileage, triggered_mileage))
        return True

    # -------------------- Triggered Mileage Persistence --------------------- #
    def _get_last_triggered_mileage(self):
        """Returns the last triggered mileage that was processed, or
        ``None`` if no record exists on disk.
        """
        if not os.path.isfile(self._last_triggered_mileage_fpath):
            return None
        try:
            with open(self._last_triggered_mileage_fpath, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def _set_last_triggered_mileage(self, mileage):
        """Saves the given triggered mileage to disk."""
        with open(self._last_triggered_mileage_fpath, "wb") as fp:
            pickle.dump(mileage, fp)

