# This module implements a TaskJob that periodically polls the Gearhead service
# for all vehicles, checks what maintenance is due (based on both current
# mileage and current time), and creates Todoist tasks for any due maintenance
# items that do not already exist.
#
# The redesigned two-phase flow uses a persistent maintenance log (via Gearhead
# Oracle endpoints) for deduplication instead of relying on Todoist task
# existence alone:
#
#   Phase B (runs first): Detect completed Todoist maintenance tasks and record
#     DONE log entries.
#   Phase A (runs second): Check for due maintenance, create Todoist tasks for
#     new items, and record PENDING log entries.

# Imports
import os
import sys
import re
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Enable import from the gearhead service directory for shared data models
gearhead_dir = os.path.join(os.path.dirname(pdir), "gearhead")
if gearhead_dir not in sys.path:
    sys.path.append(gearhead_dir)

# Service imports
from tasks.gearhead.base import TaskJob_Gearhead
from lib.config import Config, ConfigField
from lib.oracle import OracleSession, OracleSessionConfig
import lib.dtu as dtu

# Import trigger key helpers from gearhead's maintenance_log module
from maintenance_log import MaintenanceLogEntry, MAINTENANCE_METADATA_PATTERN, \
    parse_maintenance_metadata


class TaskJob_Gearhead_MaintenancePoll_Config(Config):
    """Configuration for the Gearhead maintenance poll TaskJob.

    Contains the Gearhead Oracle connection details, the polling interval,
    mileage range parameters (lookahead and buffer), datetime lookahead, the
    Todoist project name used for creating maintenance tasks, and the
    dedup cooldown period for datetime-triggered tasks.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("gearhead",                 [OracleSessionConfig],  required=True),
            ConfigField("poll_interval_days",        [int],                  required=False, default=3),
            ConfigField("mileage_lookahead",         [int],                  required=False, default=1000),
            ConfigField("mileage_buffer",            [int],                  required=False, default=500),
            ConfigField("datetime_lookahead_days",   [int],                  required=False, default=30),
            ConfigField("todoist_project",           [str],                  required=False, default="Automotive"),
            ConfigField("dedup_cooldown_days",       [int],                  required=False, default=30),
        ]


class TaskJob_Gearhead_MaintenancePoll(TaskJob_Gearhead):
    """A TaskJob that periodically polls the Gearhead service for all vehicles
    to check what maintenance is due (by mileage and/or by time), then creates
    Todoist tasks for any due maintenance items.

    Uses a two-phase approach:
      - **Phase B** (runs first): Detect completed Todoist tasks by checking
        pending log entries. Record DONE entries for completed items.
      - **Phase A** (runs second): Check for due maintenance. Create Todoist
        tasks and PENDING log entries for new items, using the maintenance log
        for deduplication instead of Todoist task existence alone.

    The poll interval, mileage lookahead/buffer, datetime lookahead, dedup
    cooldown, and Todoist project name are all configurable via
    ``maintenance_poll.json``.
    """
    def init(self):
        """Initialization hook.

        Calls the parent ``init()`` to load the base Gearhead config, then
        loads the maintenance poll-specific config from
        ``maintenance_poll.json`` and applies the configured polling interval.
        """
        super().init()

        # load the maintenance poll config
        config_fname = "maintenance_poll.json"
        config_fpath = os.path.join(fdir, config_fname)
        self.poll_config = TaskJob_Gearhead_MaintenancePoll_Config()
        self.poll_config.parse_file(config_fpath)

        # apply the configured polling interval (converted from days to
        # seconds)
        self.refresh_rate = 86400 * self.poll_config.poll_interval_days

        # override the gearhead connection with this TaskJob's own config
        # (allows the maintenance poll to use a different Gearhead instance
        # if needed, while defaulting to the same one)
        self.gearhead_config.gearhead = self.poll_config.gearhead

    def update(self):
        """Main update function implementing the two-phase maintenance poll.

        **Phase B** (Completion Detection — runs first):
          1. Fetches all vehicles from Gearhead.
          2. For each vehicle, queries pending log entries with Todoist IDs.
          3. Checks each pending entry's Todoist task via the Todoist API.
          4. If the Todoist task is gone (completed/deleted), records a DONE
             log entry in Gearhead.

        **Phase A** (Due Maintenance Check — runs second):
          1. For each vehicle, retrieves current mileage and due maintenance.
          2. For each due item, computes the trigger_key and checks the log
             for existing entries (primary dedup).
          3. For datetime tasks, applies a secondary cooling period dedup.
          4. Creates Todoist tasks with metadata footers and records PENDING
             log entries for new items.

        Returns ``True`` if the poll completed successfully (even if no new
        tasks were created); ``False`` if a critical error prevented the
        poll from running.
        """
        # ----- Step 1: Connect to Gearhead ----- #
        try:
            gearhead = self.get_gearhead_session()
        except Exception as e:
            self.log("Failed to connect to Gearhead service: %s" % e)
            return False

        # ----- Step 2: Fetch all vehicles ----- #
        try:
            r = gearhead.get("/vehicles")
            assert OracleSession.get_response_success(r), \
                "Gearhead returned a failure: %s" % \
                OracleSession.get_response_message(r)
            vehicles = OracleSession.get_response_json(r)
        except Exception as e:
            self.log("Failed to fetch vehicles from Gearhead: %s" % e)
            return False

        if not vehicles or len(vehicles) == 0:
            self.log("No vehicles configured in Gearhead. Nothing to do.")
            return True

        self.log("Retrieved %d vehicle(s) from Gearhead." % len(vehicles))

        # ----- Step 3: Get Todoist project ----- #
        try:
            project = self.get_project_by_name(
                self.poll_config.todoist_project, color="red"
            )
        except Exception as e:
            self.log("Failed to get Todoist project '%s': %s" %
                     (self.poll_config.todoist_project, e))
            return False

        todoist = self.get_todoist()

        # ===== PHASE B: Completion Detection (runs first) ===== #
        self.log("Checking for completed maintenance tasks") 
        completions_detected = 0

        for vehicle in vehicles:
            vehicle_id = vehicle.get("id", "unknown")
            vehicle_name = self._get_vehicle_display_name(vehicle)

            try:
                # Query pending log entries for this vehicle
                r = gearhead.get("/maintenance/log", payload={
                    "vehicle_id": vehicle_id,
                    "status": "pending"
                })
                if not OracleSession.get_response_success(r):
                    self.log(
                        "Failed to get pending log entries for '%s': %s" %
                        (vehicle_name,
                         OracleSession.get_response_message(r))
                    )
                    continue

                pending_entries = OracleSession.get_response_json(r)
                if not pending_entries:
                    continue

                # Filter to entries with a Todoist task ID
                entries_with_todoist = [
                    e for e in pending_entries
                    if e.get("todoist_task_id", "") != ""
                ]

                for entry in entries_with_todoist:
                    todoist_task_id = entry.get("todoist_task_id", "")
                    entry_task_id = entry.get("task_id", "unknown")
                    entry_trigger_key = entry.get("trigger_key", "")

                    # Check if the Todoist task still exists
                    task = self._get_todoist_task(
                        todoist, todoist_task_id
                    )
                    task_is_completed = False
                    if task is not None:
                        task_is_completed = task.completed_at is not None

                    # Has the task not yet been completed? If so, skip it
                    if task_is_completed is False:
                        self.log(
                            "Todoist task \"%s\" still pending for %s/%s (trigger: %s)" %
                            (todoist_task_id, vehicle_name, entry_task_id,
                             entry_trigger_key)
                        )
                        continue

                    # Otherwise, we assume the task has been completed; record
                    # its completion
                    self.log(
                        "Todoist task \"%s\" completed for %s/%s (trigger: %s)" %
                        (todoist_task_id, vehicle_name, entry_task_id,
                         entry_trigger_key)
                    )

                    # Get current mileage for the completion record
                    current_mileage = self._get_current_mileage(
                        gearhead, vehicle_id
                    )
                    if current_mileage is None:
                        current_mileage = entry.get("mileage", 0.0)

                    # POST a DONE log entry
                    try:
                        r = gearhead.post("/maintenance/log", payload={
                            "vehicle_id": vehicle_id,
                            "task_id": entry_task_id,
                            "status": "done",
                            "trigger_key": entry_trigger_key,
                            "mileage": current_mileage,
                            "notes": "Auto-detected completion from Todoist"
                        })
                        if OracleSession.get_response_success(r):
                            completions_detected += 1
                        else:
                            self.log(
                                "Failed to log completion for %s/%s: %s" %
                                (vehicle_id, entry_task_id,
                                 OracleSession.get_response_message(r))
                            )
                    except Exception as e:
                        self.log(
                            "Error logging completion for %s/%s: %s" %
                            (vehicle_id, entry_task_id, e)
                        )

            except Exception as e:
                self.log(
                    "Error during maintenance completion task checking for \"%s\": %s" %
                    (vehicle_name, e)
                )
                continue

        self.log("Detected %d completion(s)." %
                 completions_detected)

        # ===== PHASE A: Due Maintenance Check (runs second) ===== #
        self.log("Checking for due maintenance")
        tasks_created = 0

        for vehicle in vehicles:
            vehicle_id = vehicle.get("id", "unknown")
            vehicle_name = self._get_vehicle_display_name(vehicle)

            # 4a. Get current mileage
            current_mileage = self._get_current_mileage(
                gearhead, vehicle_id
            )

            # 4b. Build request params for /maintenance/due
            now = datetime.now()
            params = {"vehicle_id": vehicle_id}

            # add mileage range if we have mileage data
            if current_mileage is not None:
                mileage_start = max(
                    0, current_mileage - self.poll_config.mileage_buffer
                )
                mileage_end = (
                    current_mileage + self.poll_config.mileage_lookahead
                )
                params["mileage_start"] = mileage_start
                params["mileage_end"] = mileage_end

            # always add datetime range
            datetime_start = now
            datetime_end = dtu.add_days(
                now, self.poll_config.datetime_lookahead_days
            )
            params["datetime_start"] = datetime_start.isoformat()
            params["datetime_end"] = datetime_end.isoformat()

            # 4c. Query for due maintenance
            try:
                r = gearhead.get("/maintenance/due", payload=params)
                if not OracleSession.get_response_success(r):
                    self.log(
                        "Gearhead /maintenance/due failed for '%s': %s" %
                        (vehicle_name,
                         OracleSession.get_response_message(r))
                    )
                    continue
                due_tasks = OracleSession.get_response_json(r)
            except Exception as e:
                self.log("Failed to query due maintenance for '%s': %s" %
                         (vehicle_name, e))
                continue

            if not due_tasks:
                self.log("No due maintenance for '%s'." % vehicle_name)
                continue

            self.log("Found %d due maintenance item(s) for '%s'." %
                     (len(due_tasks), vehicle_name))

            # 4d. Create Todoist tasks for each due item
            for due_item in due_tasks:
                task_info = due_item.get("task", {})
                task_id = task_info.get("id", "unknown")
                triggered_mileages = due_item.get("triggered_mileages", [])
                triggered_datetime = due_item.get("triggered_datetime", False)

                # Compute trigger_key using static methods
                if triggered_mileages:
                    trigger_key = MaintenanceLogEntry.make_mileage_trigger_key(
                        max(triggered_mileages)
                    )
                else:
                    trigger_key = MaintenanceLogEntry.make_datetime_trigger_key(
                        now
                    )

                # PRIMARY DEDUP: Check log by trigger_key
                try:
                    r = gearhead.get("/maintenance/log", payload={
                        "vehicle_id": vehicle_id,
                        "task_id": task_id,
                        "trigger_key": trigger_key
                    })
                    if OracleSession.get_response_success(r):
                        existing_entries = OracleSession.get_response_json(r)
                        if existing_entries and len(existing_entries) > 0:
                            self.log(
                                "Already handled: %s/%s (trigger: %s)" %
                                (vehicle_name, task_id, trigger_key)
                            )
                            continue
                except Exception as e:
                    self.log(
                        "Error checking log for %s/%s: %s. "
                        "Proceeding with task creation." %
                        (vehicle_name, task_id, e)
                    )

                # SECONDARY DEDUP (datetime tasks only): Cooling period
                if not triggered_mileages and triggered_datetime:
                    try:
                        r = gearhead.get("/maintenance/log", payload={
                            "vehicle_id": vehicle_id,
                            "task_id": task_id
                        })
                        if OracleSession.get_response_success(r):
                            recent_entries = OracleSession.get_response_json(r)
                            if recent_entries and self._has_recent_entry(
                                recent_entries,
                                self.poll_config.dedup_cooldown_days
                            ):
                                self.log(
                                    "Cooling period active for %s/%s. "
                                    "Skipping." %
                                    (vehicle_name, task_id)
                                )
                                continue
                    except Exception as e:
                        self.log(
                            "Error checking cooling period for %s/%s: %s" %
                            (vehicle_name, task_id, e)
                        )

                # Build title and description with metadata footer
                title = self._build_title(vehicle, due_item)
                description = self._build_description(
                    vehicle, due_item, current_mileage,
                    vehicle_id, task_id, trigger_key
                )

                try:
                    # Create the Todoist task with a due date ~15 days out
                    due_date = dtu.set_time_end_of_day(
                        dtu.add_days(now, 15)
                    )
                    new_task = todoist.add_task(
                        title, description,
                        project_id=project.id,
                        due_datetime=due_date,
                        priority=2,
                        labels=["automotive", "maintenance"]
                    )
                    self.log("Created Todoist task: '%s'" % title)

                    # Capture the Todoist task ID
                    new_todoist_id = new_task.id if new_task else ""

                    # LOG the pending entry
                    try:
                        r = gearhead.post("/maintenance/log", payload={
                            "vehicle_id": vehicle_id,
                            "task_id": task_id,
                            "status": "pending",
                            "trigger_key": trigger_key,
                            "mileage": current_mileage if current_mileage is not None else 0.0,
                            "todoist_task_id": str(new_todoist_id),
                        })
                        if not OracleSession.get_response_success(r):
                            self.log(
                                "Warning: Failed to log pending entry for "
                                "%s/%s: %s" %
                                (vehicle_id, task_id,
                                 OracleSession.get_response_message(r))
                            )
                    except Exception as e:
                        self.log(
                            "Warning: Error logging pending entry for "
                            "%s/%s: %s" %
                            (vehicle_id, task_id, e)
                        )

                    tasks_created += 1
                except Exception as e:
                    self.log("Failed to create Todoist task '%s': %s" %
                             (title, e))
                    continue

        self.log("Maintenance poll complete. Created %d new task(s)." %
                 tasks_created)
        return True

    # ------------------------------- Helpers -------------------------------- #
    def _get_todoist_task(self, todoist, task_id):
        """Retrieves a Todoist task.

        Uses a direct API call rather than the cached task list for reliable
        completion detection.

        Args:
            todoist: The Todoist API wrapper instance.
            task_id: The Todoist task ID to check.

        Returns:
            `None` if the task was not found, or the task object if one was
            found.
        """
        try:
            task = todoist.api().get_task(task_id=task_id)
            return task
        except Exception:
            # 404 or any error means the task is gone
            return None

    def _has_recent_entry(self, entries, cooldown_days):
        """Checks whether any entry in the list has a timestamp within the
        last ``cooldown_days`` days.

        Used as the secondary dedup mechanism for datetime-triggered tasks.

        Args:
            entries: List of log entry dicts (from Gearhead API response).
            cooldown_days: Number of days for the cooling period.

        Returns:
            bool: ``True`` if any entry is within the cooling period.
        """
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=cooldown_days)
        for entry in entries:
            ts_str = entry.get("timestamp", None)
            if ts_str is not None:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts >= cutoff:
                        return True
                except (ValueError, TypeError):
                    continue
        return False

    def _get_vehicle_display_name(self, vehicle: dict) -> str:
        """Builds a human-readable display name for a vehicle.

        Prefers the first nickname if available, otherwise falls back to
        ``{year} {manufacturer}``.
        """
        nicknames = vehicle.get("nicknames", [])
        if nicknames and len(nicknames) > 0:
            return nicknames[0]

        year = vehicle.get("year", "")
        manufacturer = vehicle.get("manufacturer", "")
        return ("%s %s" % (year, manufacturer)).strip()

    def _get_current_mileage(self, gearhead, vehicle_id: str):
        """Retrieves the current mileage for a vehicle from Gearhead.

        Queries Gearhead's ``GET /mileage`` endpoint and returns the mileage
        as a float. Returns ``None`` if no mileage data is available or the
        request fails.
        """
        try:
            r = gearhead.get("/mileage", payload={
                "vehicle_id": vehicle_id
            })
            if not OracleSession.get_response_success(r):
                self.log("Failed to get mileage for '%s': %s" %
                         (vehicle_id,
                          OracleSession.get_response_message(r)))
                return None
            payload = OracleSession.get_response_json(r)
            if not payload:
                return None
            return payload.get("mileage")
        except Exception as e:
            self.log("Error fetching mileage for '%s': %s" %
                     (vehicle_id, e))
            return None

    def _build_title(self, vehicle: dict, due_item: dict) -> str:
        """Builds a descriptive Todoist task title for a due maintenance item.

        Format varies based on the trigger type:
          - Mileage trigger: ``"{task_name} - {vehicle} ({threshold} mi)"``
          - Datetime-only:   ``"{task_name} - {vehicle} ({Mon YYYY})"``

        Uses the highest triggered mileage threshold in the title to ensure
        stable deduplication across polling cycles. Datetime tasks include
        a month/year qualifier for dedup clarity.
        """
        task_info = due_item.get("task", {})
        task_name = task_info.get("name", "Maintenance")
        vehicle_name = self._get_vehicle_display_name(vehicle)

        triggered_mileages = due_item.get("triggered_mileages", [])
        if triggered_mileages:
            threshold = max(triggered_mileages)
            return "%s - %s (%s mi)" % (
                task_name, vehicle_name,
                "{:,.0f}".format(threshold)
            )

        # Datetime-only: include month/year qualifier
        now = datetime.now()
        month_str = now.strftime("%b %Y")
        return "%s - %s (%s)" % (task_name, vehicle_name, month_str)

    def _build_description(self, vehicle: dict, due_item: dict,
                           current_mileage, vehicle_id: str,
                           task_id: str, trigger_key: str) -> str:
        """Builds a description body for a Todoist maintenance task.

        Includes the maintenance task description (if any), the current
        mileage, details about which triggers matched, and a machine-readable
        metadata footer for reliable parsing during completion detection.
        """
        task_info = due_item.get("task", {})
        lines = []

        task_desc = task_info.get("description", "")
        if task_desc:
            lines.append(task_desc)

        if current_mileage is not None:
            lines.append(
                "Current mileage: {:,.0f} mi".format(current_mileage)
            )

        triggered_mileages = due_item.get("triggered_mileages", [])
        if triggered_mileages:
            thresholds = ", ".join(
                "{:,.0f}".format(m) for m in triggered_mileages
            )
            lines.append("Triggered at: %s mi" % thresholds)

        if due_item.get("triggered_datetime"):
            lines.append("Triggered by datetime schedule.")

        # Append the machine-readable metadata footer
        lines.append("")
        lines.append("---")
        lines.append(
            '[dimrod::vehicle_maintenance vehicle=%s task=%s trigger="%s"]' %
            (vehicle_id, task_id, trigger_key)
        )

        return "\n".join(lines)
