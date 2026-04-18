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
from maintenance_log import (
    MaintenanceLogEntry, MaintenanceLogEntryStatus,
    MaintenanceLogDatabase, MaintenanceLogDatabaseConfig
)


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
            ConfigField("vehicles",              [Vehicle],                      required=True),
            ConfigField("mileage_db",            [MileageDatabaseConfig],        required=True),
            ConfigField("maintenance_log_db",    [MaintenanceLogDatabaseConfig], required=True),
            ConfigField("dialogue",              [DialogueConfig],               required=False, default=None),
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

        # Initialize the maintenance log database.
        self.maintenance_log_db = MaintenanceLogDatabase(
            self.config.maintenance_log_db
        )

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

    def get_due_maintenance(self, vehicle_id,
                            mileage_start=None, mileage_end=None,
                            datetime_start=None, datetime_end=None):
        """Returns maintenance tasks for a vehicle that are due within the
        specified mileage range and/or datetime range.

        A maintenance task is included if ANY of its mileage thresholds
        falls within ``[mileage_start, mileage_end)`` (when a mileage range
        is provided), OR if any of its datetime triggers match within
        ``[datetime_start, datetime_end)`` (when a datetime range is
        provided). If both ranges are provided, either match (OR logic)
        causes the task to be included.

        Returns a list of dictionaries, each containing:
          - task:                The MaintenanceTask as JSON
          - vehicle_id:          The vehicle's ID
          - triggered_mileages:  Sorted list of thresholds within the mileage
                                 range (present only when a mileage range is
                                 provided)
          - triggered_datetime:  Boolean indicating whether any datetime
                                 trigger matched (present only when a datetime
                                 range is provided)

        Returns an empty list if the vehicle has no maintenance tasks or no
        triggers match within the given ranges.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id

        has_mileage_range = (mileage_start is not None and
                             mileage_end is not None)
        has_datetime_range = (datetime_start is not None and
                              datetime_end is not None)

        due_tasks = []

        for task in vehicle.maintenance_tasks:
            triggered_mileages = []
            triggered_datetime = False

            # --- Mileage Check ---
            if has_mileage_range and len(task.mileages) > 0:
                triggered_mileages = sorted(
                    m for m in task.mileages
                    if mileage_start <= m < mileage_end
                )

            # --- Datetime Check ---
            if has_datetime_range and len(task.datetimes) > 0:
                for trigger in task.datetimes:
                    if trigger.matches_range(datetime_start,
                                             datetime_end):
                        triggered_datetime = True
                        break  # one match is sufficient

            # --- Determine if task is due ---
            # OR logic: either mileage match or datetime match
            is_due = (len(triggered_mileages) > 0 or
                      triggered_datetime)

            if not is_due:
                continue

            result = {
                "task": task.to_json(),
                "vehicle_id": vehicle_id,
            }

            # Include mileage results if mileage range was provided
            if has_mileage_range:
                result["triggered_mileages"] = triggered_mileages

            # Include datetime result if datetime range was provided
            if has_datetime_range:
                result["triggered_datetime"] = triggered_datetime

            due_tasks.append(result)

        return due_tasks

    # ======================== Maintenance Log Methods ======================= #
    def get_maintenance_log(self, vehicle_id, task_id=None, status=None,
                            trigger_key=None, mileage_start=None,
                            mileage_end=None, time_start=None,
                            time_end=None):
        """Returns maintenance log entries for a vehicle with optional filters.

        Delegates to the appropriate ``MaintenanceLogDatabase`` method based
        on which filters are provided:
          - If ``task_id`` and ``trigger_key`` are set, uses
            ``search_by_trigger()``.
          - If ``task_id`` is set (no trigger_key), uses
            ``search_by_task()`` with optional ``status``.
          - If ``mileage_start``/``mileage_end`` are set, uses
            ``search_by_mileage_range()``.
          - If ``time_start``/``time_end`` are set, uses
            ``search_by_date_range()``.
          - If only ``status`` is set, uses ``search_by_status()``.

        Args:
            vehicle_id: Vehicle ID to query (must exist).
            task_id: Optional maintenance task ID filter.
            status: Optional ``MaintenanceLogEntryStatus`` filter.
            trigger_key: Optional trigger key filter.
            mileage_start: Optional start of mileage range (inclusive).
            mileage_end: Optional end of mileage range (inclusive).
            time_start: Optional start datetime for date range.
            time_end: Optional end datetime for date range.

        Returns:
            list[MaintenanceLogEntry]: Matching entries.

        Raises:
            AssertionError: If the vehicle ID is not found.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id

        # Resolve status if provided as string or int
        if status is not None:
            status = self._resolve_status(status)

        # Determine which query to run based on provided filters
        if task_id is not None and trigger_key is not None:
            return self.maintenance_log_db.search_by_trigger(
                vehicle_id, task_id, trigger_key
            )
        if task_id is not None:
            return self.maintenance_log_db.search_by_task(
                vehicle_id, task_id, status=status
            )
        if mileage_start is not None and mileage_end is not None:
            return self.maintenance_log_db.search_by_mileage_range(
                vehicle_id, mileage_start, mileage_end
            )
        if time_start is not None and time_end is not None:
            return self.maintenance_log_db.search_by_date_range(
                vehicle_id, time_start, time_end
            )
        if status is not None:
            return self.maintenance_log_db.search_by_status(
                vehicle_id, status
            )

        # No specific filter — return all entries via search_by_task with
        # no task_id filter. Use a broad condition.
        return self.maintenance_log_db.search_by_status(
            vehicle_id, status
        ) if status is not None else self.maintenance_log_db._query(
            vehicle_id, None
        )

    def add_maintenance_log(self, vehicle_id, task_id, status, trigger_key,
                            mileage, timestamp=None, todoist_task_id="",
                            notes=""):
        """Creates and saves a new maintenance log entry.

        After saving, if the entry has a positive mileage value, checks
        whether the vehicle's mileage should be auto-updated. If no
        mileage entries exist yet, or if the new entry's mileage exceeds
        the latest recorded mileage, a new ``MileageEntry`` is created
        and saved automatically.

        Args:
            vehicle_id: Vehicle ID (must exist).
            task_id: Maintenance task ID.
            status: Status value — accepts ``MaintenanceLogEntryStatus``,
                string names (``"pending"``, ``"done"``), or integer values
                (``0``, ``1``).
            trigger_key: Trigger key for deduplication, or ``None`` for
                ad-hoc entries without a trigger.
            mileage: Current vehicle mileage as a float.
            timestamp: Optional ISO datetime string or datetime object.
                Defaults to ``datetime.utcnow()``.
            todoist_task_id: Optional Todoist task ID string.
            notes: Optional free-text notes.

        Returns:
            MaintenanceLogEntry: The created and saved entry.

        Raises:
            AssertionError: If the vehicle ID is not found.
        """
        vehicle = self.get_vehicle(vehicle_id)
        assert vehicle is not None, \
            "Unknown vehicle ID: %s" % vehicle_id

        # Resolve status to enum
        status = self._resolve_status(status)

        # Default timestamp to now
        if timestamp is None:
            timestamp = datetime.utcnow().isoformat()
        elif isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()

        # Build the JSON payload, omitting trigger_key if None so the
        # Uniserdes default (None) is used.
        entry_json = {
            "vehicle_id": vehicle_id,
            "task_id": task_id,
            "status": status.value,
            "mileage": float(mileage),
            "timestamp": timestamp,
            "todoist_task_id": todoist_task_id,
            "notes": notes,
        }
        if trigger_key is not None:
            entry_json["trigger_key"] = trigger_key

        entry = MaintenanceLogEntry()
        entry.parse_json(entry_json)
        entry.get_id()
        self.maintenance_log_db.save(entry)

        # --- Auto-update mileage from maintenance log entries ---
        # If the entry has a positive mileage, check if this should
        # update the vehicle's latest mileage reading.
        if float(mileage) > 0:
            self._auto_update_mileage(vehicle_id, float(mileage), entry.timestamp)

        return entry

    def _auto_update_mileage(self, vehicle_id, mileage, entry_timestamp):
        """Auto-updates the vehicle's mileage if the given mileage exceeds
        the latest recorded reading.

        Creates a new ``MileageEntry`` timestamped to ``entry_timestamp``
        when no prior mileage data exists or when the new mileage value
        is greater than the latest recorded mileage.

        Args:
            vehicle_id: The vehicle ID.
            mileage: The mileage value from the maintenance log entry.
            entry_timestamp: The timestamp of the maintenance log entry.
        """
        latest = self.mileage_db.search_latest(vehicle_id)
        if latest is None or mileage > latest.mileage:
            ts = entry_timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            new_entry = MileageEntry()
            new_entry.parse_json({
                "vehicle_id": vehicle_id,
                "mileage": mileage,
                "timestamp": ts.isoformat()
            })
            new_entry.get_id()
            self.mileage_db.save(new_entry)

    def is_maintenance_pending(self, vehicle_id, task_id, trigger_key):
        """Checks if a pending entry exists for the given trigger.

        Args:
            vehicle_id: Vehicle ID.
            task_id: Maintenance task ID.
            trigger_key: Trigger key to check.

        Returns:
            bool: ``True`` if a PENDING entry exists for this trigger.
        """
        entries = self.maintenance_log_db.search_by_trigger(
            vehicle_id, task_id, trigger_key
        )
        return any(
            e.status == MaintenanceLogEntryStatus.PENDING for e in entries
        )

    def is_maintenance_done(self, vehicle_id, task_id, trigger_key):
        """Checks if a done entry exists for the given trigger.

        Args:
            vehicle_id: Vehicle ID.
            task_id: Maintenance task ID.
            trigger_key: Trigger key to check.

        Returns:
            bool: ``True`` if a DONE entry exists for this trigger.
        """
        entries = self.maintenance_log_db.search_by_trigger(
            vehicle_id, task_id, trigger_key
        )
        return any(
            e.status == MaintenanceLogEntryStatus.DONE for e in entries
        )

    def is_maintenance_handled(self, vehicle_id, task_id, trigger_key):
        """Checks if a trigger has been handled (either pending or done).

        Args:
            vehicle_id: Vehicle ID.
            task_id: Maintenance task ID.
            trigger_key: Trigger key to check.

        Returns:
            bool: ``True`` if any entry (PENDING or DONE) exists.
        """
        return self.is_maintenance_pending(vehicle_id, task_id, trigger_key) or \
               self.is_maintenance_done(vehicle_id, task_id, trigger_key)

    def has_recent_maintenance(self, vehicle_id, task_id, days=30):
        """Checks if any log entry exists for the given (vehicle, task) within
        the last N days.

        Secondary dedup mechanism for datetime tasks. Prevents the edge case
        where a datetime task spanning two months creates duplicate Todoist
        tasks.

        Args:
            vehicle_id: Vehicle ID.
            task_id: Maintenance task ID.
            days: Number of days to look back. Defaults to 30.

        Returns:
            bool: ``True`` if any entry exists within the lookback window.
        """
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        entries = self.maintenance_log_db.search_by_task(
            vehicle_id, task_id
        )
        return any(e.timestamp >= cutoff for e in entries)

    def get_pending_entries_with_todoist_ids(self):
        """Returns all pending entries across all vehicles that have a Todoist
        task ID set.

        Used by the TaskJob for completion detection.

        Returns:
            list[MaintenanceLogEntry]: Pending entries with Todoist IDs.
        """
        vehicle_ids = [v.id for v in self.config.vehicles]
        return self.maintenance_log_db.search_all_pending_with_todoist_id(
            vehicle_ids
        )

    def _resolve_status(self, status):
        """Resolves a status value to a ``MaintenanceLogEntryStatus`` enum.

        Accepts enum instances, string names, or integer values.

        Args:
            status: The status value to resolve.

        Returns:
            MaintenanceLogEntryStatus: The resolved enum value.

        Raises:
            KeyError: If a string name is invalid.
            ValueError: If an integer value is invalid.
        """
        if isinstance(status, MaintenanceLogEntryStatus):
            return status
        if isinstance(status, str):
            return MaintenanceLogEntryStatus[status.upper()]
        if isinstance(status, int):
            return MaintenanceLogEntryStatus(status)
        raise ValueError("Invalid status type: %s" % type(status))


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

        # Endpoint that returns the mileage history for a vehicle.
        @self.server.route("/mileage/history", methods=["GET"])
        def endpoint_mileage_history():
            """Returns the mileage history for a vehicle, optionally filtered
            by a date range and limited to a maximum number of entries.
            """
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(
                    msg="Must specify 'vehicle_id' string.",
                    success=False, rstatus=400)
            vid = str(flask.g.jdata["vehicle_id"])

            # Validate vehicle exists
            if self.service.get_vehicle(vid) is None:
                return self.make_response(
                    msg="Vehicle not found: %s" % vid,
                    success=False, rstatus=404)

            # Parse optional datetime range
            time_start = None
            time_end = None
            dt_start_str = flask.g.jdata.get("datetime_start", None)
            dt_end_str = flask.g.jdata.get("datetime_end", None)
            if dt_start_str is not None and dt_end_str is not None:
                try:
                    time_start = datetime.fromisoformat(str(dt_start_str))
                    time_end = datetime.fromisoformat(str(dt_end_str))
                except (ValueError, TypeError) as e:
                    return self.make_response(
                        msg="Invalid datetime format: %s" % str(e),
                        success=False, rstatus=400)

            # Parse optional limit
            limit = flask.g.jdata.get("limit", None)
            if limit is not None:
                if type(limit) not in [int]:
                    return self.make_response(
                        msg="'limit' must be an integer.",
                        success=False, rstatus=400)

            try:
                entries = self.service.get_mileage_history(
                    vid,
                    time_start=time_start,
                    time_end=time_end
                )
                # Apply limit if specified
                if limit is not None and limit > 0:
                    entries = entries[:limit]
                payload = [e.to_json() for e in entries]
                return self.make_response(success=True, payload=payload)
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
            """Returns maintenance tasks for a vehicle that are due within a
            given mileage range and/or datetime range. At least one range
            (mileage or datetime) must be provided.
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

            vid = str(flask.g.jdata["vehicle_id"])

            # --- Parse mileage range (optional pair) ---
            has_mileage_start = "mileage_start" in flask.g.jdata
            has_mileage_end = "mileage_end" in flask.g.jdata

            # Validate range pairs are complete
            if has_mileage_start != has_mileage_end:
                return self.make_response(
                    msg="Must specify both 'mileage_start' and "
                        "'mileage_end'.",
                    success=False, rstatus=400)

            mileage_start = None
            mileage_end = None
            if has_mileage_start and has_mileage_end:
                mileage_start = flask.g.jdata["mileage_start"]
                mileage_end = flask.g.jdata["mileage_end"]
                if type(mileage_start) not in [int, float]:
                    return self.make_response(
                        msg="'mileage_start' must be a number.",
                        success=False, rstatus=400)
                if type(mileage_end) not in [int, float]:
                    return self.make_response(
                        msg="'mileage_end' must be a number.",
                        success=False, rstatus=400)

            # --- Parse datetime range (optional pair) ---
            has_dt_start = "datetime_start" in flask.g.jdata
            has_dt_end = "datetime_end" in flask.g.jdata

            if has_dt_start != has_dt_end:
                return self.make_response(
                    msg="Must specify both 'datetime_start' and "
                        "'datetime_end'.",
                    success=False, rstatus=400)

            datetime_start = None
            datetime_end = None
            if has_dt_start and has_dt_end:
                try:
                    datetime_start = datetime.fromisoformat(
                        str(flask.g.jdata["datetime_start"]))
                    datetime_end = datetime.fromisoformat(
                        str(flask.g.jdata["datetime_end"]))
                except (ValueError, TypeError) as e:
                    return self.make_response(
                        msg="Invalid datetime format: %s" % str(e),
                        success=False, rstatus=400)

            # --- Validate at least one range is provided ---
            if mileage_start is None and datetime_start is None:
                return self.make_response(
                    msg="Must specify at least one range "
                        "(mileage or datetime).",
                    success=False, rstatus=400)

            try:
                due = self.service.get_due_maintenance(
                    vid,
                    mileage_start=mileage_start,
                    mileage_end=mileage_end,
                    datetime_start=datetime_start,
                    datetime_end=datetime_end
                )
                return self.make_response(success=True, payload=due)
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

        # Endpoint that retrieves maintenance log entries with optional filters.
        @self.server.route("/maintenance/log", methods=["GET"])
        def endpoint_maintenance_log_get():
            """Returns maintenance log entries for a vehicle with optional
            filters for status, task_id, trigger_key, mileage range, and
            date range.
            """
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # vehicle_id is required
            if "vehicle_id" not in flask.g.jdata:
                return self.make_response(
                    msg="Must specify 'vehicle_id' string.",
                    success=False, rstatus=400)
            vid = str(flask.g.jdata["vehicle_id"])

            # Validate vehicle exists
            if self.service.get_vehicle(vid) is None:
                return self.make_response(
                    msg="Vehicle not found: %s" % vid,
                    success=False, rstatus=404)

            # Parse optional filters
            task_id = flask.g.jdata.get("task_id", None)
            if task_id is not None:
                task_id = str(task_id)

            trigger_key = flask.g.jdata.get("trigger_key", None)
            if trigger_key is not None:
                trigger_key = str(trigger_key)

            # Parse and validate status
            status = None
            status_param = flask.g.jdata.get("status", None)
            if status_param is not None:
                try:
                    if isinstance(status_param, str):
                        status = MaintenanceLogEntryStatus[
                            status_param.upper()
                        ]
                    elif isinstance(status_param, int):
                        status = MaintenanceLogEntryStatus(status_param)
                    else:
                        raise ValueError("Invalid status type")
                except (KeyError, ValueError):
                    return self.make_response(
                        msg="Invalid status value: '%s'. Must be one of: "
                            "pending, done, 0, 1" % str(status_param),
                        success=False, rstatus=400)

            # Parse mileage range
            mileage_start = flask.g.jdata.get("mileage_start", None)
            mileage_end = flask.g.jdata.get("mileage_end", None)
            if mileage_start is not None:
                if type(mileage_start) not in [int, float]:
                    return self.make_response(
                        msg="'mileage_start' must be a number.",
                        success=False, rstatus=400)
            if mileage_end is not None:
                if type(mileage_end) not in [int, float]:
                    return self.make_response(
                        msg="'mileage_end' must be a number.",
                        success=False, rstatus=400)

            # Parse datetime range
            time_start = None
            time_end = None
            dt_start_str = flask.g.jdata.get("datetime_start", None)
            dt_end_str = flask.g.jdata.get("datetime_end", None)
            if dt_start_str is not None and dt_end_str is not None:
                try:
                    time_start = datetime.fromisoformat(str(dt_start_str))
                    time_end = datetime.fromisoformat(str(dt_end_str))
                except (ValueError, TypeError) as e:
                    return self.make_response(
                        msg="Invalid datetime format: %s" % str(e),
                        success=False, rstatus=400)

            try:
                entries = self.service.get_maintenance_log(
                    vid,
                    task_id=task_id,
                    status=status,
                    trigger_key=trigger_key,
                    mileage_start=mileage_start,
                    mileage_end=mileage_end,
                    time_start=time_start,
                    time_end=time_end
                )
                payload = [e.to_json() for e in entries]
                return self.make_response(success=True, payload=payload)
            except Exception as e:
                return self.make_response(msg=str(e),
                                          success=False, rstatus=400)

        # Endpoint that creates a new maintenance log entry.
        @self.server.route("/maintenance/log", methods=["POST"])
        def endpoint_maintenance_log_post():
            """Creates a new maintenance log entry."""
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            jdata = flask.g.jdata

            # Validate required fields
            required_fields = [
                "vehicle_id", "task_id", "status", "mileage"
            ]
            for field in required_fields:
                if field not in jdata:
                    return self.make_response(
                        msg="Must specify '%s'." % field,
                        success=False, rstatus=400)

            vid = str(jdata["vehicle_id"])
            task_id = str(jdata["task_id"])
            trigger_key = str(jdata["trigger_key"]) if "trigger_key" in jdata and jdata["trigger_key"] is not None else None

            # Validate vehicle exists
            if self.service.get_vehicle(vid) is None:
                return self.make_response(
                    msg="Vehicle not found: %s" % vid,
                    success=False, rstatus=404)

            # Validate mileage type
            mileage = jdata["mileage"]
            if type(mileage) not in [int, float]:
                return self.make_response(
                    msg="'mileage' must be a number.",
                    success=False, rstatus=400)

            # Validate and resolve status
            status_param = jdata["status"]
            try:
                if isinstance(status_param, str):
                    status = MaintenanceLogEntryStatus[
                        status_param.upper()
                    ]
                elif isinstance(status_param, int):
                    status = MaintenanceLogEntryStatus(status_param)
                else:
                    raise ValueError("Invalid status type")
            except (KeyError, ValueError):
                return self.make_response(
                    msg="Invalid status value: '%s'. Must be one of: "
                        "pending, done, 0, 1" % str(status_param),
                    success=False, rstatus=400)

            # Optional fields
            timestamp = jdata.get("timestamp", None)
            todoist_task_id = str(jdata.get("todoist_task_id", ""))
            notes = str(jdata.get("notes", ""))

            try:
                entry = self.service.add_maintenance_log(
                    vid, task_id, status, trigger_key, mileage,
                    timestamp=timestamp,
                    todoist_task_id=todoist_task_id,
                    notes=notes
                )
                return self.make_response(
                    success=True,
                    msg="Maintenance log entry created.",
                    payload=entry.to_json()
                )
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
