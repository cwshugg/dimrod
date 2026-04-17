# This module defines data models for vehicles tracked by the Gearhead service,
# including generic vehicle properties and the main Vehicle object.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField


class MaintenanceTask(Uniserdes):
    """Defines a recurring maintenance task for a vehicle, triggered by mileage
    thresholds. Each task has a unique ID, a human-readable name, an optional
    description, and a list of mileage values at which the task should be
    performed (e.g., [5000, 10000, 15000, ...]).
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("id",          [str],   required=True),
            UniserdesField("name",        [str],   required=True),
            UniserdesField("description", [str],   required=False, default=""),
            UniserdesField("mileages",    [list],  required=True),
        ]


class VehicleProperty(Uniserdes):
    """Represents a generic key-value property that can be attached to a vehicle.
    Allows open-ended metadata on vehicles. The value can be a string, integer,
    or float, enabling properties like engine type ("gasoline"), horsepower (158),
    or displacement (2.0).
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("key",       [str],              required=True),
            UniserdesField("nickname",  [str],              required=False, default=""),
            UniserdesField("value",     [str, int, float],  required=True),
        ]


class Vehicle(Uniserdes):
    """Represents a single vehicle tracked by the Gearhead service. Parsed from
    config YAML entries. Uses a list of VehicleProperty for all vehicle
    attributes (including engine details). The `nicknames` field is a list of
    human-friendly names for the vehicle (e.g., ["The Daily Driver", "The Civic"]).
    Optionally includes a list of MaintenanceTask definitions for recurring
    maintenance triggered by mileage thresholds.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("id",                  [str],              required=True),
            UniserdesField("manufacturer",        [str],              required=True),
            UniserdesField("year",                [int],              required=True),
            UniserdesField("nicknames",           [list],             required=False, default=[]),
            UniserdesField("vin",                 [str],              required=False, default=""),
            UniserdesField("license_plate",       [str],              required=False, default=""),
            UniserdesField("properties",          [VehicleProperty],  required=False, default=[]),
            UniserdesField("maintenance_tasks",   [MaintenanceTask],  required=False, default=[]),
        ]
