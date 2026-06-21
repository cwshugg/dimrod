# Implements the /groceries bot command for interacting with the Grocer
# service's grocery list.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession


# ================================= Helpers ================================== #
def _get_session(service, message):
    """Create and authenticate an OracleSession with Grocer.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    session = OracleSession(service.config.grocer)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Grocer. "
                             "It might be offline.")
        return None

    # Check the login response.
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Grocer.")
        return None
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Grocer. "
                             "(%s)" % session.get_response_message(r))
        return None

    return session


def _fetch_categories(session):
    """Fetch all grocery categories (sections) from Grocer.

    Returns a list of category dicts ({"id", "name"}), or None on failure.
    """
    r = session.post("/categories")
    if r.status_code != 200:
        return None
    return OracleSession.get_response_json(r)


def _fetch_items(session):
    """Fetch all grocery items from Grocer.

    Returns a list of item dicts ({"id", "task_id", "title", "description",
    "section_id"}), or None on failure.
    """
    r = session.post("/items")
    if r.status_code != 200:
        return None
    return OracleSession.get_response_json(r)


# ============================== Subcommands ================================= #
def _groceries_list(service, message, session):
    """Handle '/groceries' (and 'items'/'list') -- show items grouped by
    category and sorted by category name."""
    items = _fetch_items(session)
    if items is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve the grocery list "
                             "from Grocer.")
        return False

    if len(items) == 0:
        service.send_message(message.chat.id, "The grocery list is empty.")
        return True

    # Fetch categories so we can map section IDs to readable names.
    categories = _fetch_categories(session)
    if categories is None:
        categories = []
    section_names = {}
    for c in categories:
        section_names[c.get("id", None)] = c.get("name", "(Unnamed)")

    # Group items by category name. Items with an unknown or missing section
    # fall into the "Uncategorized" bucket.
    uncategorized = "Uncategorized"
    groups = {}
    for item in items:
        section_id = item.get("section_id", None)
        name = section_names.get(section_id, None)
        if name is None:
            name = uncategorized
        groups.setdefault(name, []).append(item)

    # Sort categories alphabetically, but always place "Uncategorized" last.
    def category_sort_key(name):
        return (1 if name == uncategorized else 0, name.lower())

    msg = "<b>Grocery List:</b>\n"
    for name in sorted(groups.keys(), key=category_sort_key):
        msg += "\n<b>%s</b>\n" % name
        for item in groups[name]:
            title = item.get("title", "(Untitled)")
            msg += "· %s\n" % title

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _groceries_categories(service, message, session):
    """Handle '/groceries categories' -- list all grocery categories."""
    categories = _fetch_categories(session)
    if categories is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve the categories "
                             "from Grocer.")
        return False

    if len(categories) == 0:
        service.send_message(message.chat.id, "No categories found.")
        return True

    msg = "<b>Grocery Categories:</b>\n\n"
    for c in sorted(categories, key=lambda c: c.get("name", "").lower()):
        msg += "· %s\n" % c.get("name", "(Unnamed)")

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _groceries_operation(service, message, session, endpoint, action_msg):
    """Trigger one of Grocer's update operations and report its status.

    These operations run under the service write lock and can take a few
    seconds, so send a brief acknowledgement first. The resulting status
    message is sent as a reply to that acknowledgement, so the two are
    threaded together in Telegram.
    """
    ack = service.send_message(message.chat.id, action_msg)

    # If the acknowledgement was sent successfully, reply to it with the
    # status message; otherwise, send the status message normally.
    reply_to = ack.message_id if ack is not None else None

    r = session.post(endpoint)
    if r.status_code != 200:
        # Try to surface the returned status message, if there is one.
        try:
            msg = OracleSession.get_response_message(r)
        except Exception:
            msg = None
        if msg:
            service.send_message(message.chat.id, msg,
                                 reply_to_message_id=reply_to)
        else:
            service.send_message(message.chat.id,
                                 "Sorry, that operation failed.",
                                 reply_to_message_id=reply_to)
        return False

    service.send_message(message.chat.id,
                         OracleSession.get_response_message(r),
                         reply_to_message_id=reply_to)
    return True


def _groceries_process(service, message, session):
    """Handle '/groceries process' (and 'all') -- run all three grocer
    operations in sequence: resolve recipes, deduplicate, then sort.

    Each operation behaves like its individual subcommand (sending its own
    acknowledgement and reporting completion as a reply). This is a
    best-effort sequence: if one operation fails, the remaining operations
    are still attempted, and the overall result reflects whether every
    operation succeeded.
    """
    operations = [
        ("/items/resolve_recipes", "Resolving recipes into ingredients..."),
        ("/items/deduplicate", "Deduplicating the grocery list..."),
        ("/items/sort", "Sorting the grocery list..."),
    ]

    success = True
    for endpoint, action_msg in operations:
        if not _groceries_operation(service, message, session,
                                    endpoint, action_msg):
            success = False
    return success


def _groceries_help(service, message):
    """Send usage help for the /groceries command."""
    msg = "<b>Usage:</b> <code>/groceries [subcommand]</code>\n\n"
    msg += "<b>Commands:</b>\n"
    msg += "  <code>/groceries</code>"
    msg += " — List grocery items, grouped by category\n"
    msg += "  <code>/groceries categories</code>"
    msg += " — List the grocery categories\n"
    msg += "  <code>/groceries sort</code>"
    msg += " — Auto-sort items into categories\n"
    msg += "  <code>/groceries dedup</code>"
    msg += " — Remove duplicate items\n"
    msg += "  <code>/groceries resolve</code>"
    msg += " — Resolve recipes into ingredients\n"
    msg += "  <code>/groceries process</code>"
    msg += " — Resolve recipes, deduplicate, then sort\n"
    msg += "\n<b>Aliases:</b> /grocery, /grocer, /groc, /g\n"
    msg += "\n<b>Examples:</b>\n"
    msg += "  <code>/groceries</code>\n"
    msg += "  <code>/groceries categories</code>\n"
    msg += "  <code>/groceries sort</code>"
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# =================================== Main =================================== #
def command_groceries(service, message, args: list):
    """Main handler for the /groceries command.

    Routes to the appropriate subcommand based on the arguments provided.
    Supports listing items by category, listing categories, and triggering
    Grocer's sort/deduplicate/resolve operations.
    """
    # Make sure the grocer integration is configured.
    if service.config.grocer is None:
        service.send_message(message.chat.id,
                             "The grocery integration isn't configured.")
        return False

    # Establish a session with Grocer.
    session = _get_session(service, message)
    if session is None:
        return False

    # No arguments -- list all items grouped by category.
    if len(args) <= 1:
        return _groceries_list(service, message, session)

    subcommand = args[1].strip().lower()

    # /groceries help
    if subcommand == "help":
        _groceries_help(service, message)
        return True

    # /groceries items | list
    if subcommand in ["items", "list"]:
        return _groceries_list(service, message, session)

    # /groceries categories | cats
    if subcommand in ["categories", "cats"]:
        return _groceries_categories(service, message, session)

    # /groceries sort
    if subcommand == "sort":
        return _groceries_operation(service, message, session,
                                    "/items/sort",
                                    "Sorting the grocery list...")

    # /groceries dedup | deduplicate
    if subcommand in ["dedup", "deduplicate"]:
        return _groceries_operation(service, message, session,
                                    "/items/deduplicate",
                                    "Deduplicating the grocery list...")

    # /groceries resolve | recipes | resolve_recipes
    if subcommand in ["resolve", "recipes", "resolve_recipes"]:
        return _groceries_operation(service, message, session,
                                    "/items/resolve_recipes",
                                    "Resolving recipes into ingredients...")

    # /groceries process | all
    if subcommand in ["process", "all"]:
        return _groceries_process(service, message, session)

    # Unknown subcommand -- show help.
    service.send_message(message.chat.id,
                         "Unknown subcommand: <code>%s</code>" % subcommand,
                         parse_mode="HTML")
    _groceries_help(service, message)
    return False
