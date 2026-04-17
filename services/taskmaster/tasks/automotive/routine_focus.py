# This module defines routine mileage-based maintenance TaskJobs for the
# Ford Focus. Each subclass maps to a specific Gearhead MaintenanceTask
# (by vehicle_id and maintenance_task_id) and relies on Gearhead to determine
# when the maintenance is actually due based on real mileage data.

# Imports
import os
import sys

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from tasks.automotive.routine import *


class TaskJob_Automotive_Routine_Maintenance_Focus_5k(
        TaskJob_Automotive_Routine_Maintenance):
    """5k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_5k`` maintenance task as
    due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_5k"
        self.title = "5k-Mile Car Maintenance - Focus"
        content_fname = "%s_5k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_10k(
        TaskJob_Automotive_Routine_Maintenance):
    """10k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_10k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_10k"
        self.title = "10k-Mile Car Maintenance - Focus"
        content_fname = "%s_10k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_20k(
        TaskJob_Automotive_Routine_Maintenance):
    """20k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_20k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_20k"
        self.title = "20k-Mile Car Maintenance - Focus"
        content_fname = "%s_20k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_30k(
        TaskJob_Automotive_Routine_Maintenance):
    """30k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_30k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_30k"
        self.title = "30k-Mile Car Maintenance - Focus"
        content_fname = "%s_30k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_50k(
        TaskJob_Automotive_Routine_Maintenance):
    """50k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_50k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_50k"
        self.title = "50k-Mile Car Maintenance - Focus"
        content_fname = "%s_50k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_60k(
        TaskJob_Automotive_Routine_Maintenance):
    """60k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_60k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_60k"
        self.title = "60k-Mile Car Maintenance - Focus"
        content_fname = "%s_60k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)


class TaskJob_Automotive_Routine_Maintenance_Focus_80k(
        TaskJob_Automotive_Routine_Maintenance):
    """80k-mile routine maintenance for the Focus.

    Triggered when Gearhead reports the ``routine_80k`` maintenance task
    as due for the ``focus`` vehicle.
    """
    def init(self):
        """Set vehicle, maintenance task, and content file."""
        super().init()
        self.vehicle_id = "focus"
        self.maintenance_task_id = "routine_80k"
        self.title = "80k-Mile Car Maintenance - Focus"
        content_fname = "%s_80k.md" % __file__.replace(".py", "")
        self.content = os.path.join(fdir, content_fname)

