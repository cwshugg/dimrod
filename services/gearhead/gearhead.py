#!/usr/bin/python3
# This module implements the Gearhead service for tracking vehicle and mileage
# data within DImROD. It provides HTTP endpoints and NLA (Natural Language
# Actions) for querying and updating vehicle/mileage information.

# Imports
import os
import sys
import re
import flask
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.nla import NLAEndpoint, NLAEndpointInvokeParameters, NLAResult
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface

# Service imports
from vehicle import Vehicle, VehicleProperty, MaintenanceTask
from mileage import MileageEntry, MileageDatabase, MileageDatabaseConfig


# =============================== Config Class =============================== #
class GearheadConfig(ServiceConfig):
    """Configuration for the Gearhead service. Extends ServiceConfig with
    vehicle definitions, mileage database settings, and optional dialogue
    configuration for LLM-assisted vehicle matching.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        fields = [
            ConfigField("vehicles",     [Vehicle],                  required=True),
            ConfigField("mileage_db",   [MileageDatabaseConfig],    required=True),
            ConfigField("dialogue",     [DialogueConfig],           required=False, default=None),
        ]
        self.fields += fields


# ================================= Service ================================== #
class GearheadService(Service):
    """The main Gearhead service class. Manages vehicle definitions loaded from
    config and a mileage database for recording and querying odometer readings.
    """
    def __init__(self, config_path):
        """Constructor."""
        super().__init__(config_path)
        self.config = GearheadConfig()
        self.config.parse_file(config_path)

        # Vehicles are already parsed as Vehicle objects by Uniserdes.
        # Validate uniqueness of vehicle IDs.
        self.vehicles = self.config.vehicles
        vehicle_ids = set()
        for v in self.vehicles:
            assert v.id not in vehicle_ids, \
                "Duplicate vehicle ID found: %s" % v.id
            vehicle_ids.add(v.id)
            self.log.write("Loaded vehicle: %s" % v.id)

        # Initialize the mileage database.
        self.mileage_db = MileageDatabase(self.config.mileage_db)

    def run(self):
        """Overridden main function implementation."""
        super().run()

    def get_vehicles(self):
        """Returns the full list of configured vehicles."""
        return self.vehicles

    def get_vehicle(self, vehicle_id):
        """Looks up a vehicle by its ID string. Returns None if not found."""
        for v in self.vehicles:
            if v.id == vehicle_id:
                return v
        return None

    def get_mileage(self, vehicle_id):
        """Retrieves the latest mileage entry for a vehicle from the database.
        Returns None if no mileage data exists.
        """
        return self.mileage_db.search_latest(vehicle_id)

    def get_mileage_history(self, vehicle_id, time_start=None, time_end=None):
        """Retrieves the full mileage history for a vehicle, optionally filtered
        by date range. Returns a list of MileageEntry objects.
        """
        return self.mileage_db.search_history(vehicle_id,
                                               time_start=time_start,
                                               time_end=time_end)

    def set_mileage(self, vehicle_id, mileage):
        """Creates a new MileageEntry timestamped to now and saves it to the
        database. Validates that the vehicle exists first. Returns the created
        entry.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id

        entry = MileageEntry()
        entry.parse_json({
            "vehicle_id": vehicle_id,
            "mileage": float(mileage),
            "timestamp": datetime.now().isoformat()
        })
        entry.get_id()
        self.mileage_db.save(entry)
        return entry

    def get_maintenance_tasks(self, vehicle_id):
        """Returns the list of MaintenanceTask objects for a vehicle.
        Returns an empty list if the vehicle has no maintenance tasks defined.
        Raises AssertionError if the vehicle ID is not found.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id
        return vehicle.maintenance_tasks

    def get_due_maintenance(self, vehicle_id, mileage_start, mileage_end):
        """Returns maintenance tasks for a vehicle that have any threshold
        mileage within [mileage_start, mileage_end).

        A maintenance task is included if ANY of its mileage thresholds
        falls within the range [mileage_start, mileage_end) (inclusive start,
        exclusive end).

        Returns a list of dictionaries, each containing:
          - task:                The MaintenanceTask as JSON
          - vehicle_id:          The vehicle's ID
          - triggered_mileages:  Sorted list of thresholds within the range

        Returns an empty list if the vehicle has no maintenance tasks or no
        thresholds fall within the range.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id

        due_tasks = []

        for task in vehicle.maintenance_tasks:
            # Find all thresholds in [mileage_start, mileage_end).
            triggered = sorted(
                m for m in task.mileages
                if mileage_start <= m < mileage_end
            )

            if not triggered:
                continue

            due_tasks.append({
                "task": task.to_json(),
                "vehicle_id": vehicle_id,
                "triggered_mileages": triggered,
            })

        return due_tasks


# ============================== Service Oracle ============================== #
class GearheadOracle(Oracle):
    """Oracle for the Gearhead service. Defines HTTP endpoints and NLA
    registration for vehicle and mileage operations.
    """
    def endpoints(self):
        """Endpoint definition function."""
        super().endpoints()

        # Endpoint that returns all configured vehicles.
        @self.server.route("/vehicles", methods=["GET"])
        def endpoint_vehicles():
            """Returns a JSON array of all configured vehicles."""
            if not flask.g.user:
                return self.make_response(rstatus=404)

            vehicles = []
            for v in self.service.get_vehicles():
                vehicles.append(v.to_json())
            return self.make_response(success=True, payload=vehicles)

        # Endpoint that returns a single vehicle by ID.
        @self.server.route("/vehicle", methods=["GET"])
        def endpoint_vehicle():
            """Returns a single vehicle's full details by ID."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            if "id" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'id' string.",
                                          success=False, rstatus=400)
            vid = str(flask.g.jdata["id"])

            vehicle = self.service.get_vehicle(vid)
            if vehicle is None:
                return self.make_response(msg="Vehicle not found: %s" % vid,
                                          success=False, rstatus=404)
            return self.make_response(success=True, payload=vehicle.to_json())

        # Endpoint that returns the latest mileage for a vehicle.
        @self.server.route("/mileage", methods=["GET"])
        def endpoint_mileage_get():
            """Returns the latest (current) mileage for a vehicle."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'vehicle_id' string.",
                                          success=False, rstatus=400)
            vid = str(flask.g.jdata["vehicle_id"])

            entry = self.service.get_mileage(vid)
            if entry is None:
                return self.make_response(success=True, payload={})
            return self.make_response(success=True, payload=entry.to_json())

        # Endpoint that records a new mileage reading.
        @self.server.route("/mileage", methods=["POST"])
        def endpoint_mileage_post():
            """Records a new mileage reading for a vehicle."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'vehicle_id' string.",
                                          success=False, rstatus=400)
            if "mileage" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'mileage' number.",
                                          success=False, rstatus=400)

            vid = str(flask.g.jdata["vehicle_id"])
            mileage = flask.g.jdata["mileage"]
            if type(mileage) not in [int, float]:
                return self.make_response(msg="'mileage' must be a number.",
                                          success=False, rstatus=400)

            try:
                entry = self.service.set_mileage(vid, mileage)
                return self.make_response(success=True,
                                          msg="Mileage recorded successfully.",
                                          payload=entry.to_json())
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

        # Endpoint that returns maintenance tasks for a vehicle.
        @self.server.route("/maintenance", methods=["GET"])
        def endpoint_maintenance():
            """Returns the list of maintenance tasks for a vehicle."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(msg="Must specify 'vehicle_id' string.",
                                          success=False, rstatus=400)
            vid = str(flask.g.jdata["vehicle_id"])

            try:
                tasks = self.service.get_maintenance_tasks(vid)
                payload = [t.to_json() for t in tasks]
                return self.make_response(success=True, payload=payload)
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

        # Endpoint that returns due/overdue maintenance tasks.
        @self.server.route("/maintenance/due", methods=["GET"])
        def endpoint_maintenance_due():
            """Returns maintenance tasks for a vehicle that have any threshold
            mileage within [mileage_start, mileage_end).
            """
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # Validate required fields.
            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(
                    msg="Must specify 'vehicle_id' string.",
                    success=False, rstatus=400)
            if "mileage_start" not in flask.g.jdata:
                return self.make_response(
                    msg="Must specify 'mileage_start' number.",
                    success=False, rstatus=400)
            if "mileage_end" not in flask.g.jdata:
                return self.make_response(
                    msg="Must specify 'mileage_end' number.",
                    success=False, rstatus=400)

            vid = str(flask.g.jdata["vehicle_id"])
            mileage_start = flask.g.jdata["mileage_start"]
            mileage_end = flask.g.jdata["mileage_end"]

            # Validate that mileage values are numeric.
            if type(mileage_start) not in [int, float]:
                return self.make_response(
                    msg="'mileage_start' must be a number.",
                    success=False, rstatus=400)
            if type(mileage_end) not in [int, float]:
                return self.make_response(
                    msg="'mileage_end' must be a number.",
                    success=False, rstatus=400)

            try:
                due = self.service.get_due_maintenance(
                    vid, mileage_start, mileage_end
                )
                return self.make_response(success=True, payload=due)
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

    def init_nla(self):
        """Registers NLA endpoints for vehicle and mileage operations."""
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                "name": "list_vehicles",
                "description": "List all vehicles that Gearhead knows about, "
                               "including their nicknames and basic info."
            }).set_handler(nla_list_vehicles),
            NLAEndpoint.from_json({
                "name": "get_mileage",
                "description": "Get the current mileage reading for a vehicle. "
                               "Phrases like how many miles, what is the mileage, "
                               "odometer for, etc."
            }).set_handler(nla_get_mileage),
            NLAEndpoint.from_json({
                "name": "set_mileage",
                "description": "Update or record a new mileage reading for a vehicle. "
                               "Phrases like set mileage for X to Y, "
                               "update mileage, X has Y miles, etc."
            }).set_handler(nla_set_mileage),
        ]


# =============================== NLA Helpers ================================ #
def _match_vehicle_by_substring(user_text, vehicles):
    """Attempts to match a vehicle by checking if any vehicle ID, any of its
    nicknames, or manufacturer appears as a substring in the user text
    (case-insensitive).

    Returns the matched Vehicle, or None if no match is found.
    """
    text_lower = user_text.lower()
    for v in vehicles:
        # Check vehicle ID (e.g. "civic_2020")
        if v.id.lower() in text_lower:
            return v
        # Check all nicknames (e.g. "the daily driver", "the civic")
        for nick in v.nicknames:
            if nick.lower() in text_lower:
                return v
        # Check manufacturer (e.g. "honda")
        if v.manufacturer.lower() in text_lower:
            return v
    return None


def _match_vehicle_by_llm(oracle, user_text, vehicles):
    """Falls back to an LLM oneshot to identify which vehicle the user is
    referring to. Returns the matched Vehicle, or None.

    Only called when:
    1. Substring matching has already failed
    2. oracle.service.config.dialogue is not None
    """
    # Build a vehicle summary for the LLM prompt
    vehicle_list_str = ""
    for v in vehicles:
        vehicle_list_str += "- ID: \"%s\", Manufacturer: %s, Year: %d" % (
            v.id, v.manufacturer, v.year
        )
        if v.nicknames:
            vehicle_list_str += ", Nicknames: %s" % \
                ", ".join("\"%s\"" % n for n in v.nicknames)
        vehicle_list_str += "\n"

    intro = (
        "You are a helper that identifies which vehicle a user is referring to. "
        "You will be given a user message and a list of known vehicles. "
        "Respond with ONLY the vehicle ID that best matches the user intent. "
        "If you cannot determine which vehicle the user means, respond with "
        "the exact text NONE."
    )
    prompt = (
        "Known vehicles:\n%s\n"
        "User message: \"%s\"\n\n"
        "Which vehicle ID is the user referring to?"
    ) % (vehicle_list_str, user_text)

    # Create a DialogueInterface on-the-fly (Lumen pattern)
    dialogue = DialogueInterface(oracle.service.config.dialogue)
    result = dialogue.oneshot(intro, prompt)

    # Parse the LLM response -- look for a matching vehicle ID
    result = result.strip().strip('"')
    if result == "NONE":
        return None
    for v in vehicles:
        if v.id == result:
            return v
    # If the LLM returned something that is not an exact ID, try substring
    result_lower = result.lower()
    for v in vehicles:
        if v.id.lower() in result_lower or result_lower in v.id.lower():
            return v
    return None


def _match_vehicle(oracle, user_text, vehicles):
    """Two-tier vehicle matching -- substring first, LLM fallback second.
    Returns the matched Vehicle, or None.
    """
    # Tier 1: substring matching
    vehicle = _match_vehicle_by_substring(user_text, vehicles)
    if vehicle is not None:
        return vehicle

    # Tier 2: LLM fallback (only if dialogue is configured)
    if oracle.service.config.dialogue is not None:
        vehicle = _match_vehicle_by_llm(oracle, user_text, vehicles)
    return vehicle


# =============================== NLA Handlers =============================== #
def nla_list_vehicles(oracle, jdata):
    """NLA handler that lists all configured vehicles with their basic info."""
    params = NLAEndpointInvokeParameters.from_json(jdata)

    vehicles = oracle.service.get_vehicles()
    vehicle_strs = []
    for v in vehicles:
        desc = "• %s - %s %d" % (v.id, v.manufacturer, v.year)
        if v.nicknames:
            desc += " (%s)" % ", ".join("\"%s\"" % n for n in v.nicknames)
        vehicle_strs.append(desc)
    msg = "List of vehicles:\n" + "\n".join(vehicle_strs)

    return NLAResult.from_json({
        "success": True,
        "message": msg
    })


def nla_get_mileage(oracle, jdata):
    """NLA handler that retrieves the current mileage for a vehicle identified
    from the user's natural language message.
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    user_text = params.message
    if params.substring:
        user_text = params.substring

    vehicles = oracle.service.get_vehicles()
    vehicle = _match_vehicle(oracle, user_text, vehicles)

    if vehicle is None:
        names = ["%s (%s)" % (", ".join(v.nicknames) if v.nicknames else v.id,
                              v.manufacturer) for v in vehicles]
        return NLAResult.from_json({
            "success": False,
            "message": "I could not figure out which vehicle you mean. "
                       "I know about: %s" % ", ".join(names)
        })

    entry = oracle.service.get_mileage(vehicle.id)
    if entry is None:
        return NLAResult.from_json({
            "success": True,
            "message": "No mileage data recorded yet for %s." %
                       (vehicle.nicknames[0] if vehicle.nicknames else vehicle.id)
        })
    return NLAResult.from_json({
        "success": True,
        "message": "The current mileage for %s is %.1f miles." %
                   (vehicle.nicknames[0] if vehicle.nicknames else vehicle.id,
                    entry.mileage)
    })


def nla_set_mileage(oracle, jdata):
    """NLA handler that records a new mileage reading for a vehicle identified
    from the user's natural language message. Extracts the mileage number from
    the message text.
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    user_text = params.message

    vehicles = oracle.service.get_vehicles()
    vehicle = _match_vehicle(oracle, user_text, vehicles)

    if vehicle is None:
        names = ["%s (%s)" % (", ".join(v.nicknames) if v.nicknames else v.id,
                              v.manufacturer) for v in vehicles]
        return NLAResult.from_json({
            "success": False,
            "message": "I could not figure out which vehicle you mean. "
                       "I know about: %s" % ", ".join(names)
        })

    # Extract numeric mileage value -- take the largest number found
    numbers = re.findall(r"[\d,]+\.?\d*", user_text)
    numbers = [float(n.replace(",", "")) for n in numbers]
    if len(numbers) == 0:
        return NLAResult.from_json({
            "success": False,
            "message": "I could not find a mileage number in your message."
        })
    mileage = max(numbers)

    entry = oracle.service.set_mileage(vehicle.id, mileage)
    return NLAResult.from_json({
        "success": True,
        "message": "Recorded mileage for %s: %.1f miles." %
                   (vehicle.nicknames[0] if vehicle.nicknames else vehicle.id,
                    entry.mileage)
    })


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=GearheadConfig,
                     service=GearheadService,
                     oracle=GearheadOracle)
    cli.run()
