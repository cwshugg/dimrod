# Implements the /recipes bot command for interacting with Chef recipes.

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
    """Create and authenticate an OracleSession with Chef.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    session = OracleSession(service.config.chef)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Chef. "
                             "It might be offline.")
        return None

    # Check the login response.
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Chef.")
        return None
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Chef. "
                             "(%s)" % session.get_response_message(r))
        return None

    return session


def _fetch_recipes(session):
    """Fetch all recipes from Chef.

    Returns a dictionary mapping recipe IDs to recipe summary dicts,
    or None on failure.
    """
    r = session.post("/recipes/list_all")
    if r.status_code != 200:
        return None
    return OracleSession.get_response_json(r)


def _fetch_recipe_by_id(session, recipe_id):
    """Fetch a single recipe by ID from Chef.

    Returns the full recipe dict, or None if not found or on failure.
    """
    r = session.post("/recipes/get_by_id", payload={"id": recipe_id})
    if r.status_code != 200:
        return None
    return OracleSession.get_response_json(r)


def _format_ingredient(ingredient):
    """Format a single ingredient dict for display.

    Returns a string like '1.0 lb ground beef' or '2.0 cups elbow macaroni'.
    """
    quantity = ingredient.get("quantity", 1.0)

    # Format the quantity as an integer, if it's a whole number, or as a float
    # otherwise.
    quantity_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)

    title = ingredient.get("title", None) or ingredient.get("id", "unknown")
    return "(<i>%sx</i>) %s" % (quantity_str, title)


def _list_recipe_ids(session, service, message):
    """Send a message listing available recipe IDs.

    Used for error recovery when a recipe ID is not found.
    """
    recipes = _fetch_recipes(session)
    if recipes and len(recipes) > 0:
        msg = "Available recipes:\n"
        for r_id, recipe in sorted(recipes.items()):
            title = recipe.get("title", "(Untitled)")
            msg += "· %s [<code>%s</code>]\n" % (title, r_id)
        service.send_message(message.chat.id, msg, parse_mode="HTML")


# ============================== Subcommands ================================= #
def _recipes_list(service, message, session):
    """Handle '/recipes' with no arguments -- list all recipes with IDs."""
    recipes = _fetch_recipes(session)
    if recipes is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve recipe data "
                             "from Chef.")
        return False

    if len(recipes) == 0:
        service.send_message(message.chat.id, "No recipes found.")
        return True

    msg = "<b>Available Recipes:</b>\n\n"
    for r_id, recipe in sorted(recipes.items()):
        title = recipe.get("title", "(Untitled)")
        description = recipe.get("description", "")
        msg += "· <b>%s</b> (<code>%s</code>)\n" % (title, r_id)
        if description:
            msg += "    · Description: %s\n" % description

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _recipes_detail(service, message, session, recipe_id):
    """Handle '/recipes <id>' -- show full details for a recipe."""
    recipe = _fetch_recipe_by_id(session, recipe_id)
    if recipe is None:
        service.send_message(message.chat.id,
                             "Recipe not found: <code>%s</code>" % recipe_id,
                             parse_mode="HTML")
        _list_recipe_ids(session, service, message)
        return False

    title = recipe.get("title", "(Untitled)")
    icon = recipe.get("icon", None)
    msg = "%s<b>%s</b> [<code>%s</code>]\n" % (
        "" if icon is None else "%s " % icon,
        title,
        recipe_id
    )

    # Description, if present.
    description = recipe.get("description", None)
    if description and len(description) > 0:
        msg += "<i>%s</i>\n" % description

    # Servings, if present.
    servings = recipe.get("servings", None)
    if servings is not None:
        msg += "\nMakes <b>%s serving%s</b>\n" % (
            servings,
            "" if servings == 1 else "s"
        )

    # Links, if present.
    links = recipe.get("links", [])
    if links and len(links) > 0:
        msg += "\n<b>Links:</b>\n"
        for link in links:
            msg += "· %s\n" % link

    # Ingredients section.
    ingredients = recipe.get("ingredients", [])
    if ingredients and len(ingredients) > 0:
        msg += "\n<b>Ingredients:</b>\n"
        for ing in ingredients:
            msg += "· %s\n" % _format_ingredient(ing)

    # Steps section.
    steps = recipe.get("steps", [])
    if steps and len(steps) > 0:
        msg += "\n<b>Steps:</b>\n"
        for i, step in enumerate(steps, start=1):
            step_text = step.get("description", None) \
                        or step.get("title", None) \
                        or step.get("id", "unknown")
            msg += "%d. %s\n" % (i, step_text)

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


def _recipes_help(service, message):
    """Send usage help for the /recipes command."""
    msg = "<b>Usage:</b> <code>/recipes [id]</code>\n\n"
    msg += "<b>Commands:</b>\n"
    msg += "  <code>/recipes</code>"
    msg += " — List all available recipes\n"
    msg += "  <code>/recipes &lt;id&gt;</code>"
    msg += " — Show ingredients and steps for a recipe\n"
    msg += "\n<b>Aliases:</b> /recipe\n"
    msg += "\n<b>Examples:</b>\n"
    msg += "  <code>/recipes</code>\n"
    msg += "  <code>/recipes chili_mac</code>"
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# =================================== Main =================================== #
def command_recipes(service, message, args: list):
    """Main handler for the /recipes command.

    Routes to the appropriate subcommand based on the arguments provided.
    Supports listing all recipes or viewing full details for a single recipe.
    """
    # Establish a session with Chef.
    session = _get_session(service, message)
    if session is None:
        return False

    # No arguments -- list all recipes.
    if len(args) <= 1:
        return _recipes_list(service, message, session)

    subcommand = args[1].strip().lower()

    # /recipes help
    if subcommand == "help":
        _recipes_help(service, message)
        return True

    # /recipes <id> -- treat the argument as a recipe ID.
    return _recipes_detail(service, message, session, subcommand)

