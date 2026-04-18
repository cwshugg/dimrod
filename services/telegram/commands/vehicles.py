# Implements the /vehicles bot command for interacting with Gearhead vehicles.

# Imports
import os
import re
import sys
from datetime import datetime, timedelta

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession


# ================================= Helpers ================================== #
def _get_session(service, message):
    """Create and authenticate an OracleSession with Gearhead.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    session = OracleSession(service.config.gearhead)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Gearhead. "
                             "It might be offline.")
        return None

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Gearhead.")
        return None
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Gearhead. "
                             "(%s)" % session.get_response_message(r))
        return None

    return session


def _fetch_vehicles(session):
    """Fetch all vehicles from Gearhead.

    Returns a list of vehicle dicts, or None on failure.
    """
    r = session.get("/vehicles")
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _fetch_mileage(session, vehicle_id):
    """Fetch the latest mileage for a vehicle.

    Returns the mileage entry dict, or None if unavailable.
    """
    r = session.get("/mileage", payload={"vehicle_id": vehicle_id})
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    data = session.get_response_json(r)
    if not data:
        return None
    return data


def _fetch_mileage_history(session, vehicle_id, limit=15):
    """Fetch mileage history entries for a vehicle.

    Returns a list of mileage entry dicts (newest first), or None on
    failure.
    """
    payload = {"vehicle_id": vehicle_id, "limit": limit}
    r = session.get("/mileage/history", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _fetch_vehicle(session, vehicle_id):
    """Fetch a single vehicle by ID.

    Returns the vehicle dict, or None if not found.
    """
    r = session.get("/vehicle", payload={"id": vehicle_id})
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _fetch_maintenance_due(session, vehicle_id, mileage_start, mileage_end,
                           datetime_start, datetime_end):
    """Fetch upcoming maintenance tasks for a vehicle.

    Returns a list of due-maintenance dicts, or None on failure.
    """
    payload = {
        "vehicle_id": vehicle_id,
        "mileage_start": mileage_start,
        "mileage_end": mileage_end,
        "datetime_start": datetime_start.isoformat(),
        "datetime_end": datetime_end.isoformat(),
    }
    r = session.get("/maintenance/due", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _fetch_maintenance_log(session, vehicle_id):
    """Fetch maintenance log entries for a vehicle.

    Returns a list of log entry dicts, or None on failure.
    """
    payload = {"vehicle_id": vehicle_id}
    r = session.get("/maintenance/log", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _format_mileage(mileage_val):
    """Format a mileage number with commas and 'mi' suffix."""
    return "{:,.0f} mi".format(float(mileage_val))


def _vehicle_display_name(vehicle):
    """Return a human-friendly display name for a vehicle dict.

    Example: 'Focus St (2018 Ford)'.
    """
    vid = vehicle.get("id", "unknown")
    year = vehicle.get("year", "")
    manufacturer = vehicle.get("manufacturer", "")
    # Build a readable name from the ID (replace underscores, title-case)
    name = vid.replace("_", " ").title()
    # Remove trailing year if it appears in the name (e.g. "Focus St 2018")
    year_str = str(year)
    if name.endswith(year_str):
        name = name[:-len(year_str)].strip()
    return "%s (%s %s)" % (name, year, manufacturer)


def _list_vehicle_ids(session, service, message):
    """Send a message listing available vehicle IDs.

    Used for error recovery when a vehicle ID is not found.
    """
    vehicles = _fetch_vehicles(session)
    if vehicles and len(vehicles) > 0:
        msg = "Available vehicles:\n"
        for v in vehicles:
            msg += "· <code>%s</code>\n" % v.get("id", "unknown")
        service.send_message(message.chat.id, msg, parse_mode="HTML")


def _format_timestamp(timestamp_str):
    """Format an ISO timestamp string for display.

    Returns a string like '2026-04-15 14:30'.
    """
    try:
        dt = datetime.fromisoformat(timestamp_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return timestamp_str or "unknown"


def _find_vehicle(user_input, vehicles):
    """Fuzzy-match a vehicle from user input against a list of vehicle dicts.

    Matching strategy:
      1. Exact ID match (case-insensitive).
      2. Case-insensitive substring match against vehicle IDs and nicknames.
      3. Returns match results to let the caller decide what to do.

    Args:
        user_input: The user-provided string to match against.
        vehicles: A list of vehicle dicts (from the Gearhead API).

    Returns:
        A tuple of ``(vehicle, matches)``:
          - If exactly one vehicle matches: ``(vehicle_dict, [])``
          - If multiple vehicles match: ``(None, [list of matching dicts])``
          - If no vehicles match: ``(None, [])``
    """
    if not vehicles or not user_input:
        return (None, [])

    input_lower = user_input.strip().lower()

    # Pass 1: exact ID match (case-insensitive)
    for v in vehicles:
        if v.get("id", "").lower() == input_lower:
            return (v, [])

    # Pass 2: substring match against IDs and nicknames
    matches = []
    for v in vehicles:
        vid = v.get("id", "").lower()
        nicknames = v.get("nicknames", [])
        matched = False

        # Check if input is a substring of the vehicle ID
        if input_lower in vid:
            matched = True

        # Check if input is a substring of any nickname
        if not matched:
            for nick in nicknames:
                if input_lower in nick.lower():
                    matched = True
                    break

        if matched and v not in matches:
            matches.append(v)

    if len(matches) == 1:
        return (matches[0], [])
    if len(matches) > 1:
        return (None, matches)
    return (None, [])


def _resolve_vehicle(service, message, session, user_input):
    """Resolve a vehicle from user input using fuzzy matching.

    Fetches the vehicle list from Gearhead, then applies ``_find_vehicle``
    to fuzzy-match the user's input. Sends appropriate error messages on
    failure.

    Args:
        service: The Telegram bot service instance.
        message: The incoming Telegram message.
        session: An authenticated ``OracleSession`` with Gearhead.
        user_input: The user-provided vehicle identifier string.

    Returns:
        A tuple of ``(vehicle_dict, vehicle_id)``:
          - On success: ``(vehicle_dict, vehicle_id_string)``
          - On failure: ``(None, None)`` (error message already sent)
    """
    vehicles = _fetch_vehicles(session)
    if vehicles is None or len(vehicles) == 0:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve vehicle data "
                             "from Gearhead.")
        return (None, None)

    vehicle, matches = _find_vehicle(user_input, vehicles)

    if vehicle is not None:
        return (vehicle, vehicle.get("id"))

    if len(matches) > 1:
        msg = "Multiple vehicles match '<code>%s</code>':\n" % user_input
        for m in matches:
            msg += "· <code>%s</code> — %s\n" % (
                m.get("id", "unknown"),
                _vehicle_display_name(m)
            )
        msg += "\nPlease be more specific."
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return (None, None)

    # No match
    service.send_message(message.chat.id,
                         "Vehicle not found: <code>%s</code>" % user_input,
                         parse_mode="HTML")
    _list_vehicle_ids(session, service, message)
    return (None, None)


def _parse_mileage(text):
    """Parse the first number (int or float) from a text string.

    Uses regex to extract the first numeric value found, handling formats
    like ``"45000"``, ``"45,000"``, ``"45000.5"``, and
    ``"about 45000 miles"``.

    Args:
        text: The text to parse a mileage number from.

    Returns:
        The parsed float, or ``None`` if no valid number is found.
    """
    if text is None:
        return None
    match = re.search(r'[\d,]+\.?\d*', text)
    if match is None:
        return None
    try:
        value = float(match.group().replace(',', ''))
    except (ValueError, TypeError):
        return None
    if value < 0:
        return None
    return value


# ============================== Subcommands ================================= #
def _vehicles_list(service, message, session):
    """Handle '/vehicles' with no arguments -- list all vehicles with mileage."""
    vehicles = _fetch_vehicles(session)
    if vehicles is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve vehicle data from Gearhead.")
        return False

    if len(vehicles) == 0:
        service.send_message(message.chat.id, "No vehicles found.")
        return True

    msg = "<b>Your Vehicles:</b>\n"
    for v in vehicles:
        vid = v.get("id", "unknown")
        display_name = _vehicle_display_name(v)

        # fetch current mileage
        mileage_entry = _fetch_mileage(session, vid)
        mileage_str = "(unknown mileage)"
        if mileage_entry and "mileage" in mileage_entry:
            mileage_str = _format_mileage(mileage_entry["mileage"])

        year = v.get("year", "")
        manufacturer = v.get("manufacturer", "")

        # Build a readable short name from the ID
        name = vid.replace("_", " ").title()
        year_str = str(year)
        if name.endswith(year_str):
            name = name[:-len(year_str)].strip()

        msg += "\n<b>%s</b> (<code>%s</code>)" % (name, vid)
        msg += "\n· %s %s | %s" % (year, manufacturer, mileage_str)

        # add nicknames if present
        nicknames = v.get("nicknames", [])
        if nicknames and len(nicknames) > 0:
            msg += "\n· Nicknames: %s" % ", ".join(nicknames)

        msg += "\n"

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _vehicles_get(service, message, session, user_input):
    """Handle '/vehicles get <id>' -- show detailed info for a vehicle."""
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    display_name = _vehicle_display_name(vehicle)

    # fetch current mileage
    mileage_entry = _fetch_mileage(session, vehicle_id)
    mileage_str = "unknown"
    if mileage_entry and "mileage" in mileage_entry:
        mileage_str = _format_mileage(mileage_entry["mileage"])

    # build detailed message
    msg = "<b>%s</b>\n\n" % display_name

    msg += "<b>Details:</b>\n"
    msg += "· ID: <code>%s</code>\n" % vehicle.get("id", "unknown")
    msg += "· Manufacturer: %s\n" % vehicle.get("manufacturer", "N/A")
    msg += "· Year: %s\n" % vehicle.get("year", "N/A")

    vin = vehicle.get("vin", "")
    if vin:
        msg += "· VIN: <code>%s</code>\n" % vin

    plate = vehicle.get("license_plate", "")
    if plate:
        msg += "· License Plate: <code>%s</code>\n" % plate

    msg += "\n<b>Current Mileage:</b> %s\n" % mileage_str

    # nicknames
    nicknames = vehicle.get("nicknames", [])
    if nicknames and len(nicknames) > 0:
        msg += "\n<b>Nicknames:</b>\n"
        for nick in nicknames:
            msg += "· %s\n" % nick

    # properties - show ALL, sorted alphabetically by key
    properties = vehicle.get("properties", [])
    if properties and len(properties) > 0:
        sorted_props = sorted(properties,
                              key=lambda p: p.get("key", "").lower())
        msg += "\n<b>Properties:</b>\n"
        for prop in sorted_props:
            pname = prop.get("nickname", "") or prop.get("key", "unknown")
            pvalue = prop.get("value", "N/A")
            msg += "· %s: <code>%s</code>\n" % (pname, pvalue)

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _vehicles_get_mileage_list(service, message, session, user_input):
    """Handle '/vehicles get <id> mileage' -- show mileage history."""
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    entries = _fetch_mileage_history(session, vehicle_id, limit=15)
    if entries is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve mileage history "
                             "from Gearhead.")
        return False

    if len(entries) == 0:
        service.send_message(message.chat.id,
                             "No mileage history found for <code>%s</code>."
                             % vehicle_id,
                             parse_mode="HTML")
        return True

    msg = "<b>Mileage History (<code>%s</code>):</b>\n\n" % vehicle_id
    for entry in entries:
        mileage = _format_mileage(entry.get("mileage", 0))
        timestamp = _format_timestamp(entry.get("timestamp", ""))
        msg += "· %s - %s\n" % (timestamp, mileage)

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _vehicles_get_maintenance_due(service, message, session, user_input):
    """Handle '/vehicles get <id> maintenance due' -- show upcoming maintenance."""
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    # get current mileage
    mileage_entry = _fetch_mileage(session, vehicle_id)
    if mileage_entry is None or "mileage" not in mileage_entry:
        service.send_message(message.chat.id,
                             "No current mileage data available for "
                             "<code>%s</code>. Cannot determine upcoming "
                             "maintenance." % vehicle_id,
                             parse_mode="HTML")
        return False

    current_mileage = float(mileage_entry["mileage"])
    mileage_end = current_mileage + 1000
    now = datetime.now()
    datetime_end = now + timedelta(days=30)

    due_tasks = _fetch_maintenance_due(session, vehicle_id,
                                       current_mileage, mileage_end,
                                       now, datetime_end)
    if due_tasks is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve maintenance data "
                             "from Gearhead.")
        return False

    if len(due_tasks) == 0:
        service.send_message(message.chat.id,
                             "No maintenance due in the next 1,000 miles "
                             "or 30 days.")
        return True

    msg = "<b>Upcoming Maintenance (<code>%s</code>):</b>\n\n" % vehicle_id
    for item in due_tasks:
        task = item.get("task", {})
        task_name = task.get("name", "Unknown Task")

        # determine the trigger description
        triggered_mileages = item.get("triggered_mileages", [])
        triggered_datetime = item.get("triggered_datetime", False)

        trigger_parts = []
        if triggered_mileages:
            nearest = triggered_mileages[0]
            trigger_parts.append("due at %s" % _format_mileage(nearest))
        if triggered_datetime:
            trigger_parts.append("due by %s" % datetime_end.strftime(
                "%Y-%m-%d"))

        trigger_str = " / ".join(trigger_parts) if trigger_parts else "due"
        msg += "· %s - %s\n" % (task_name, trigger_str)

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _vehicles_get_maintenance_log(service, message, session, user_input):
    """Handle '/vehicles get <id> maintenance log' -- show maintenance log."""
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    entries = _fetch_maintenance_log(session, vehicle_id)
    if entries is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve the maintenance log "
                             "from Gearhead.")
        return False

    if len(entries) == 0:
        service.send_message(message.chat.id,
                             "No maintenance log entries found for "
                             "<code>%s</code>." % vehicle_id,
                             parse_mode="HTML")
        return True

    # Limit to 15 most recent entries (entries should already be ordered
    # by timestamp descending from the API)
    display_entries = entries[:15]

    msg = "<b>Maintenance Log (<code>%s</code>):</b>\n\n" % vehicle_id
    for entry in display_entries:
        task_id = entry.get("task_id", "Unknown")
        status_val = entry.get("status", 0)
        status_str = "DONE" if status_val == 1 else "PENDING"
        mileage = _format_mileage(entry.get("mileage", 0))
        timestamp = _format_timestamp(entry.get("timestamp", ""))

        msg += "· %s - <code>%s</code> [<code>%s</code>] - %s\n" % (
            timestamp,
            task_id,
            status_str,
            mileage,
        )

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _vehicles_set_mileage(service, message, session, user_input, mileage_str):
    """Handle '/vehicles set <id> mileage <number>' -- update mileage."""
    # validate mileage is a number
    try:
        mileage = float(mileage_str.replace(",", ""))
        if mileage < 0:
            raise ValueError("Mileage must be positive.")
    except ValueError:
        service.send_message(message.chat.id,
                             "Invalid mileage value: <code>%s</code>\n"
                             "Please provide a positive number." % mileage_str,
                             parse_mode="HTML")
        return False

    # fuzzy-match the vehicle
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    # post the mileage update
    payload = {
        "vehicle_id": vehicle_id,
        "mileage": mileage
    }
    r = session.post("/mileage", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        err_msg = "Unknown error"
        try:
            err_msg = session.get_response_message(r)
        except Exception:
            pass
        service.send_message(message.chat.id,
                             "Failed to update mileage. (%s)" % err_msg)
        return False

    display_name = _vehicle_display_name(vehicle)
    service.send_message(message.chat.id,
                         "Updated %s mileage to %s" %
                         (display_name, _format_mileage(mileage)))
    return True


def _vehicles_gas(service, message, session, user_input, mileage_str,
                  notes):
    """Handle '/vehicles gas <id> <mileage> [notes]' -- log a gas refill.

    Posts a maintenance log entry with task_id ``"gas_refill"`` and status
    ``"done"`` to Gearhead. The mileage is parsed from the provided
    string, and any remaining text is captured as notes.

    Args:
        service: The Telegram bot service instance.
        message: The incoming Telegram message.
        session: An authenticated OracleSession with Gearhead.
        user_input: The user-provided vehicle identifier string.
        mileage_str: The mileage string to parse.
        notes: Free-text notes for the gas refill.

    Returns:
        bool: ``True`` on success, ``False`` on failure.
    """
    # parse mileage
    mileage = _parse_mileage(mileage_str)
    if mileage is None:
        service.send_message(message.chat.id,
                             "Invalid mileage value: <code>%s</code>\n"
                             "Please provide a positive number."
                             % mileage_str,
                             parse_mode="HTML")
        return False

    # fuzzy-match the vehicle
    vehicle, vehicle_id = _resolve_vehicle(service, message, session,
                                           user_input)
    if vehicle is None:
        return False

    # post the maintenance log entry
    payload = {
        "vehicle_id": vehicle_id,
        "task_id": "gas_refill",
        "status": "done",
        "mileage": mileage,
        "notes": notes,
    }
    r = session.post("/maintenance/log", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        err_msg = "Unknown error"
        try:
            err_msg = session.get_response_message(r)
        except Exception:
            pass
        service.send_message(message.chat.id,
                             "Failed to log gas refill. (%s)" % err_msg)
        return False

    display_name = _vehicle_display_name(vehicle)
    confirm_msg = "Logged gas refill for %s at %s" % (
        display_name, _format_mileage(mileage)
    )
    if notes:
        confirm_msg += " - %s" % notes
    service.send_message(message.chat.id, confirm_msg)
    return True


def _vehicles_help(service, message):
    """Send usage help for the /vehicles command."""
    msg = "<b>Usage:</b> <code>/vehicles [subcommand] [args...]</code>\n\n"
    msg += "<b>Commands:</b>\n"
    msg += "  <code>/vehicles</code> — List all vehicles and current mileage\n"
    msg += "  <code>/vehicles get &lt;id&gt;</code>"
    msg += " — Get detailed info for a vehicle\n"
    msg += "  <code>/vehicles get &lt;id&gt; mileage</code>"
    msg += " — Show recent mileage history\n"
    msg += "  <code>/vehicles get &lt;id&gt; maintenance due</code>"
    msg += " — Show upcoming maintenance\n"
    msg += "  <code>/vehicles get &lt;id&gt; maintenance log</code>"
    msg += " — Show maintenance log\n"
    msg += "  <code>/vehicles set &lt;id&gt; mileage &lt;number&gt;</code>"
    msg += " — Update a vehicle's mileage\n"
    msg += "  <code>/vehicles gas &lt;id&gt; &lt;mileage&gt; [notes]</code>"
    msg += " — Log a gas refill\n"
    msg += "\n<b>Vehicle Matching:</b>\n"
    msg += "  Vehicle IDs support fuzzy matching. You can use a partial ID\n"
    msg += "  or nickname instead of the full vehicle ID.\n"
    msg += "\n<b>Aliases:</b> /v, /vehicle\n"
    msg += "\n<b>Examples:</b>\n"
    msg += "  <code>/vehicles</code>\n"
    msg += "  <code>/vehicles get focus</code>\n"
    msg += "  <code>/v get focus mileage</code>\n"
    msg += "  <code>/vehicle get focus maintenance due</code>\n"
    msg += "  <code>/vehicles set focus mileage 45000</code>\n"
    msg += "  <code>/vehicles gas focus 45230 Shell station, regular</code>"
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# =================================== Main =================================== #
def command_vehicles(service, message, args: list):
    """Main handler for the /vehicles command.

    Routes to the appropriate subcommand based on the arguments provided.
    Supports listing vehicles, getting vehicle details, mileage history,
    maintenance due, maintenance log, and setting mileage.
    """
    # establish a session with Gearhead
    session = _get_session(service, message)
    if session is None:
        return False

    # no subcommand -- list all vehicles
    if len(args) <= 1:
        return _vehicles_list(service, message, session)

    subcommand = args[1].strip().lower()

    # /vehicles help
    if subcommand == "help":
        _vehicles_help(service, message)
        return True

    # /vehicles get <id> [sub-subcommand...]
    if subcommand == "get":
        if len(args) < 3:
            service.send_message(message.chat.id,
                                 "Missing vehicle ID.\n")
            _vehicles_help(service, message)
            return False
        vehicle_id = args[2].strip()

        # Check for single-word sub-subcommands (4 args)
        if len(args) == 4:
            sub_sub = args[3].strip().lower()

            # /vehicles get <id> mileage
            if sub_sub == "mileage":
                return _vehicles_get_mileage_list(service, message,
                                                   session, vehicle_id)

            # unknown single-word sub-subcommand
            service.send_message(message.chat.id,
                                 "Unknown subcommand: "
                                 "<code>%s</code>" % sub_sub,
                                 parse_mode="HTML")
            _vehicles_help(service, message)
            return False

        # Check for two-word sub-subcommands (5+ args)
        if len(args) >= 5:
            sub_sub = args[3].strip().lower()
            sub_sub_action = args[4].strip().lower()

            # /vehicles get <id> maintenance due
            if sub_sub == "maintenance" and sub_sub_action == "due":
                return _vehicles_get_maintenance_due(service, message,
                                                      session, vehicle_id)

            # /vehicles get <id> maintenance log
            if sub_sub == "maintenance" and sub_sub_action == "log":
                return _vehicles_get_maintenance_log(service, message,
                                                      session, vehicle_id)

            # unknown two-word sub-subcommand
            service.send_message(message.chat.id,
                                 "Unknown subcommand: "
                                 "<code>%s %s</code>" % (sub_sub,
                                                          sub_sub_action),
                                 parse_mode="HTML")
            _vehicles_help(service, message)
            return False

        # plain /vehicles get <id>
        return _vehicles_get(service, message, session, vehicle_id)

    # /vehicles set <id> mileage <number>
    if subcommand == "set":
        if len(args) < 5:
            service.send_message(message.chat.id,
                                 "Missing arguments.\n")
            _vehicles_help(service, message)
            return False
        vehicle_id = args[2].strip()
        field = args[3].strip().lower()
        if field != "mileage":
            service.send_message(message.chat.id,
                                 "Unknown field: <code>%s</code>. "
                                 "Currently only <code>mileage</code> "
                                 "is supported." % field,
                                 parse_mode="HTML")
            return False
        mileage_str = args[4].strip()
        return _vehicles_set_mileage(service, message, session,
                                     vehicle_id, mileage_str)

    # /vehicles gas <id> <mileage> [notes...]
    if subcommand == "gas":
        if len(args) < 4:
            service.send_message(message.chat.id,
                                 "Missing arguments.\n"
                                 "Usage: <code>/vehicles gas &lt;id&gt; "
                                 "&lt;mileage&gt; [notes]</code>",
                                 parse_mode="HTML")
            return False
        vehicle_id = args[2].strip()
        mileage_str = args[3].strip()
        # Remaining args (index 4+) are captured as notes
        notes = " ".join(a.strip() for a in args[4:]) if len(args) > 4 else ""
        return _vehicles_gas(service, message, session,
                             vehicle_id, mileage_str, notes)

    # unknown subcommand
    service.send_message(message.chat.id,
                         "Unknown subcommand: <code>%s</code>" % subcommand,
                         parse_mode="HTML")
    _vehicles_help(service, message)
    return False
