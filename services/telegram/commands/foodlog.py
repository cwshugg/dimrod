# Implements the /foodlog bot command for interacting with Munchbook.

# Imports
import os
import sys
import time
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
import lib.dtu as dtu


# ================================= Helpers ================================== #
def _get_session(service, message):
    """Create and authenticate an OracleSession with Munchbook.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    if not hasattr(service.config, 'munchbook') or \
            service.config.munchbook is None:
        service.send_message(message.chat.id,
                             "Munchbook is not configured for this bot.")
        return None
    session = OracleSession(service.config.munchbook)
    try:
        r = session.login()
    except Exception:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Munchbook. "
                             "It might be offline.")
        return None

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Munchbook.")
        return None
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Munchbook. "
                             "(%s)" % session.get_response_message(r))
        return None

    return session


def _get_chat_name(service, message):
    """Look up and return the configured Telegram chat name."""
    chat_id = str(message.chat.id)
    for chat in service.chats:
        if chat.id == chat_id:
            return chat.name
    return None


def _match_user_database(chat_name, users_list):
    """Match a munchbook user against the given chat name.

    Performs case-insensitive substring matching. Only user names with
    3 or more characters are considered to avoid spurious matches.
    """
    if chat_name is None:
        return None

    # Note: returns the first matching user. If multiple users match the
    # chat name, the first one in the list wins. User names should be
    # distinct enough to avoid ambiguity.
    chat_lower = chat_name.lower()
    for user in users_list:
        user_name = str(user.get("name", ""))
        if len(user_name) < 3:
            continue
        if user_name.lower() in chat_lower:
            return user
    return None


def _fetch_users(session):
    """Fetch the list of accessible Munchbook users."""
    r = session.get("/users/list")
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _search_entries(session, user_name, start_ts, end_ts, count=None):
    """Search Munchbook entries for a given user and time range."""
    payload = {
        "user_name": user_name,
        "start": start_ts,
        "end": end_ts,
    }
    if count is not None:
        payload["count"] = count

    r = session.post("/entries/search", payload=payload)
    if r.status_code != 200 or not session.get_response_success(r):
        return None
    return session.get_response_json(r)


def _add_entry(session, user_name, description, notes, timestamp):
    """Add a Munchbook entry for a given user."""
    payload = {
        "user_name": user_name,
        "description": description,
        "notes": notes,
        "timestamp": timestamp,
    }
    return session.post("/entries/add", payload=payload)


def _delete_entry(session, user_name, entry_id):
    """Delete a Munchbook entry by ID."""
    payload = {
        "user_name": user_name,
        "entry_id": entry_id,
    }
    return session.post("/entries/delete", payload=payload)


def _resolve_auto_user(service, message, users_list):
    """Resolve a Munchbook user automatically from the Telegram chat name."""
    chat_name = _get_chat_name(service, message)
    if chat_name is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't determine this chat's name.")
        return None

    user = _match_user_database(chat_name, users_list)
    if user is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't determine which Munchbook "
                             "user matches this chat.")
        return None
    return user


def _format_timestamp(timestamp):
    """Format a Unix timestamp for display."""
    return datetime.utcfromtimestamp(int(timestamp)).strftime(
        "%Y-%m-%d %H:%M:%S") + " UTC"


def _format_entries(title, user_name, entries):
    """Format a list of Munchbook entries into a readable message."""
    msg = "<b>%s</b> (%s):\n\n" % (title, user_name)
    for entry in entries:
        msg += "· %s\n" % _format_timestamp(entry.get("timestamp", 0))
        msg += "  <b>%s</b>\n" % entry.get("description", "")
        notes = entry.get("notes", "")
        if notes:
            msg += "  <i>%s</i>\n" % notes
        ingredients = entry.get("ingredients", [])
        if ingredients and len(ingredients) > 0:
            msg += "  🥗 Ingredients:\n"
            for ing in ingredients:
                msg += "    • <code>%s</code>\n" % ing
        entry_id = entry.get("entry_id", "")
        if entry_id:
            msg += "  ID: <code>%s</code>\n" % entry_id
        msg += "\n"
    return msg.strip()


# ============================== Subcommands ================================= #
def _foodlog_list(service, message, session):
    """Handle '/foodlog' and '/foodlog list'."""
    users = _fetch_users(session)
    if users is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve user data from "
                             "Munchbook.")
        return False

    if len(users) == 0:
        service.send_message(message.chat.id,
                             "No accessible Munchbook users were found.")
        return True

    msg = "<b>Available Munchbook Users:</b>\n"
    for user in users:
        msg += "\n• <code>%s</code>" % user.get("name", "unknown")
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _foodlog_recent(service, message, session, args):
    """Handle '/foodlog recent [user_name] [count]'."""
    users = _fetch_users(session)
    if users is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve user data from "
                             "Munchbook.")
        return False

    user_name = None
    count = 10

    if len(args) >= 3:
        arg = args[2].strip()
        # If the argument is a number, treat it as count; otherwise treat it
        # as a user name. This means numeric-only user names are not supported
        # with positional arguments — use the explicit search subcommand
        # instead.
        if arg.isdigit():
            count = int(arg)
        else:
            user_name = arg

    if len(args) >= 4:
        try:
            count = int(args[3].strip())
        except ValueError:
            service.send_message(message.chat.id,
                                 "Count must be an integer.")
            return False

    if user_name is None:
        user = _resolve_auto_user(service, message, users)
        if user is None:
            return False
        user_name = user["name"]

    entries = _search_entries(session, user_name, 0, int(time.time()),
                              count=count)
    if entries is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve recent entries "
                             "from Munchbook.")
        return False

    if len(entries) == 0:
        service.send_message(message.chat.id,
                             "No food entries found for %s." % user_name)
        return True

    msg = _format_entries("Recent Food Entries", user_name, entries)
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _try_parse_datetime(text):
    """Attempt to parse a string as a datetime.

    Supported formats:
      - ``YYYY-MM-DD HH:MM`` or ``YYYY-MM-DD HH:MM:SS`` (24-hour)
      - ``YYYY-MM-DD`` (date only, time defaults to 00:00)
      - ``YYYY-MM-DD 1:30pm`` or ``YYYY-MM-DD 9pm`` (date + 12-hour time)
      - ``1:30pm`` or ``9pm`` (12-hour time, date defaults to today)

    Returns the Unix timestamp (int) on success, or None if parsing fails.
    """
    text = text.strip()

    # try standard 24-hour formats first
    formats = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    # try a date + 12-hour clock time (e.g. "2026-06-09 1:30pm")
    parts = text.split(None, 1)
    if len(parts) == 2:
        datestamp = dtu.parse_yyyymmdd(parts[0])
        clocktime = dtu.parse_time_clock(parts[1])
        if datestamp is not None and clocktime is not None:
            dt = datetime(datestamp[0], datestamp[1], datestamp[2],
                          hour=clocktime[0], minute=clocktime[1])
            return int(dt.timestamp())

    # try date-only (e.g. "2026-06-09")
    datestamp = dtu.parse_yyyymmdd(text)
    if datestamp is not None:
        dt = datetime(datestamp[0], datestamp[1], datestamp[2])
        return int(dt.timestamp())

    # try 12-hour clock time only (e.g. "1:30pm", "9pm")
    # uses today's date as the base
    clocktime = dtu.parse_time_clock(text)
    if clocktime is not None:
        now = datetime.now()
        dt = now.replace(hour=clocktime[0], minute=clocktime[1],
                         second=0, microsecond=0)
        return int(dt.timestamp())

    return None


def _foodlog_entry(service, message, session, text):
    """Handle '/foodlog entry FOOD DESCRIPTION. NOTES'.

    Also supports an optional datetime override:
    '/foodlog entry YYYY-MM-DD HH:MM. FOOD DESCRIPTION. NOTES'
    If the first segment after 'entry' parses as a datetime, it is used
    as the timestamp; otherwise it is treated as the food description.
    """
    if not text.lower().startswith("entry"):
        service.send_message(message.chat.id,
                             "Usage: <code>/foodlog entry "
                             "FOOD DESCRIPTION. NOTES</code>",
                             parse_mode="HTML")
        return False

    remainder = text[len("entry"):].strip()
    # strip a leading period if the user includes one out of habit
    if remainder.startswith("."):
        remainder = remainder[1:].strip()
    if len(remainder) == 0:
        service.send_message(message.chat.id,
                             "Please provide a food description after "
                             "<code>entry</code>",
                             parse_mode="HTML")
        return False

    # split into segments separated by "."
    parts = remainder.split(".")
    parts = [p.strip() for p in parts]

    # try to parse the first segment as a datetime
    timestamp = None
    parsed_dt = _try_parse_datetime(parts[0])
    if parsed_dt is not None:
        # first segment is a datetime; description is the next segment
        timestamp = parsed_dt
        parts = parts[1:]

    # after removing the optional datetime, parts[0] is the description
    # and the rest (joined) are notes
    if len(parts) == 0 or len(parts[0]) == 0:
        service.send_message(message.chat.id,
                             "Food description cannot be empty.")
        return False

    description = parts[0].strip()
    notes = ". ".join(p for p in parts[1:] if len(p) > 0).strip()

    # default to the message timestamp if no datetime was provided
    if timestamp is None:
        timestamp = int(message.date)

    users = _fetch_users(session)
    if users is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve user data from "
                             "Munchbook.")
        return False

    user = _resolve_auto_user(service, message, users)
    if user is None:
        return False

    r = _add_entry(session, user["name"], description, notes, timestamp)
    if r.status_code != 200 or not session.get_response_success(r):
        err_msg = "Unknown error"
        try:
            err_msg = session.get_response_message(r)
        except Exception:
            pass
        service.send_message(message.chat.id,
                             "Failed to log food entry. (%s)" % err_msg)
        return False

    # extract the entry ID and ingredients from the response
    entry_id = ""
    ingredients = []
    try:
        resp_data = session.get_response_json(r)
        entry_id = resp_data.get("entry_id", "")
        ingredients = resp_data.get("ingredients", [])
    except Exception:
        pass

    confirm_msg = "Logged food entry for %s at %s:\n" % (
        user["name"], _format_timestamp(timestamp))
    confirm_msg += "<b>%s</b>" % description
    if notes:
        confirm_msg += " — <i>%s</i>" % notes
    if ingredients and len(ingredients) > 0:
        confirm_msg += "\n🥗 Ingredients:"
        for ing in ingredients:
            confirm_msg += "\n  • <code>%s</code>" % ing
    if entry_id:
        confirm_msg += "\n\nID: <code>%s</code>" % entry_id
    service.send_message(message.chat.id, confirm_msg, parse_mode="HTML")
    return True


def _foodlog_search(service, message, session, args):
    """Handle '/foodlog search <user_name> <start> <end> [count]'."""
    if len(args) < 5:
        service.send_message(message.chat.id,
                             "Usage: <code>/foodlog search &lt;user_name&gt; "
                             "&lt;start_timestamp&gt; &lt;end_timestamp&gt; "
                             "[count]</code>",
                             parse_mode="HTML")
        return False

    user_name = args[2].strip()
    try:
        start_ts = int(args[3].strip())
        end_ts = int(args[4].strip())
    except ValueError:
        service.send_message(message.chat.id,
                             "Start and end timestamps must be integers.")
        return False

    count = None
    if len(args) >= 6:
        try:
            count = int(args[5].strip())
        except ValueError:
            service.send_message(message.chat.id,
                                 "Count must be an integer.")
            return False

    entries = _search_entries(session, user_name, start_ts, end_ts,
                              count=count)
    if entries is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't search Munchbook entries.")
        return False

    if len(entries) == 0:
        service.send_message(message.chat.id,
                             "No food entries found for %s in that range."
                             % user_name)
        return True

    msg = _format_entries("Search Results", user_name, entries)
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _foodlog_delete(service, message, session, args):
    """Handle '/foodlog delete <entry_id>'."""
    if len(args) < 3:
        service.send_message(message.chat.id,
                             "Usage: <code>/foodlog delete "
                             "&lt;entry_id&gt;</code>",
                             parse_mode="HTML")
        return False

    entry_id = args[2].strip()

    users = _fetch_users(session)
    if users is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve user data "
                             "from Munchbook.")
        return False

    # try to delete from each accessible user's database
    for user in users:
        r = _delete_entry(session, user["name"], entry_id)
        if r.status_code == 200 and \
                session.get_response_success(r):
            service.send_message(
                message.chat.id,
                "Deleted entry <code>%s</code> from %s."
                % (entry_id, user["name"]),
                parse_mode="HTML")
            return True

    service.send_message(message.chat.id,
                         "Entry <code>%s</code> not found in any "
                         "accessible database." % entry_id,
                         parse_mode="HTML")
    return False


def _foodlog_help(service, message):
    """Send usage help for the /foodlog command."""
    msg = "<b>Usage:</b> <code>/foodlog [subcommand] [args...]</code>\n\n"
    msg += "<b>Commands:</b>\n"
    msg += "  <code>/foodlog</code> — List accessible Munchbook users\n"
    msg += "  <code>/foodlog list</code> — List accessible Munchbook users\n"
    msg += "  <code>/foodlog recent [user_name] [count]</code>"
    msg += " — Show recent food entries\n"
    msg += "  <code>/foodlog entry FOOD DESCRIPTION. NOTES</code>"
    msg += " — Quick-add a food entry\n"
    msg += "  <code>/foodlog entry YYYY-MM-DD HH:MM. FOOD. NOTES</code>"
    msg += " — Quick-add with a custom timestamp\n"
    msg += "  <code>/foodlog entry 1:30pm. FOOD. NOTES</code>"
    msg += " — Quick-add with a 12-hour time (today)\n"
    msg += "  <code>/foodlog search &lt;user_name&gt; "
    msg += "&lt;start_timestamp&gt; &lt;end_timestamp&gt; [count]</code>"
    msg += " — Search entries manually\n"
    msg += "  <code>/foodlog delete &lt;entry_id&gt;</code>"
    msg += " — Delete a food entry by ID\n"
    msg += "  <code>/foodlog help</code> — Show this help message\n"
    msg += "\n<b>Aliases:</b> /food, /f, /munchbook\n"
    msg += "\n<b>Examples:</b>\n"
    msg += "  <code>/foodlog</code>\n"
    msg += "  <code>/foodlog recent</code>\n"
    msg += "  <code>/foodlog recent cwshugg 5</code>\n"
    msg += "  <code>/foodlog entry Turkey sandwich. Lunch after workout</code>\n"
    msg += "  <code>/foodlog entry 2026-06-09 12:30. Grilled chicken. Meal prep</code>\n"
    msg += "  <code>/foodlog entry 1:30pm. Protein shake. Post-workout</code>\n"
    msg += "  <code>/foodlog search cwshugg 0 2000000000 20</code>\n"
    msg += "  <code>/foodlog delete abc123def456...</code>\n"
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# =================================== Main =================================== #
def command_foodlog(service, message, args: list):
    """Main handler for the /foodlog command."""
    session = _get_session(service, message)
    if session is None:
        return False

    # /foodlog entry FOOD DESCRIPTION. NOTES
    if len(args) > 1:
        text = " ".join(args[1:]).strip()
        if text.lower().startswith("entry"):
            return _foodlog_entry(service, message, session, text)

    # /foodlog and /foodlog list
    if len(args) <= 1:
        return _foodlog_list(service, message, session)

    subcommand = args[1].strip().lower()

    if subcommand == "help":
        _foodlog_help(service, message)
        return True

    if subcommand == "list":
        return _foodlog_list(service, message, session)

    if subcommand == "recent":
        return _foodlog_recent(service, message, session, args)

    if subcommand == "search":
        return _foodlog_search(service, message, session, args)

    if subcommand == "delete":
        return _foodlog_delete(service, message, session, args)

    _foodlog_help(service, message)
    return False
