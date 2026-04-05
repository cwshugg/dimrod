# Implements the /recipes bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from lumen.light import LightConfig, Light

# Main function.
def command_recipes(service, message, args: list):
    # create a HTTP session with chef
    session = OracleSession(service.config.chef)
    try:
        r = session.login()
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Chef. "
                             "It might be offline.")
        return False

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Chef.")
        return False
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Chef. "
                             "(%s)" % session.get_response_message(r))
        return False

    # retrieve a list of lights and convert them into objects
    r = session.post("/recipes/list_all")
    recipes = OracleSession.get_response_json(r)

    # If no recipes were returned by Chef, message and return early.
    if len(recipes) == 0:
        msg = "No recipes found."
        service.send_message(message.chat.id, msg)
        return True

    # Generate a list of recipe names and descriptions to present to the user
    # as a Telegram message.
    msg = "Found %d recipes:\n\n" % len(recipes)
    for (recipe_id, recipe) in recipes.items():
        # Extract recipe details:
        recipe_title = recipe.get("title", "(Untitled Recipe)")
        recipe_desc = recipe.get("description", None)

        # Compose a message:
        recipe_msg = "• <b>%s</b>" % recipe_title
        if recipe_desc is not None and len(recipe_desc) > 0:
            recipe_msg += " - <i>%s</i>" % recipe_desc

        # Does the recipe have any links? Attach them as sub-bullets.
        links = recipe.get("links", None)
        if links is not None and isinstance(links, list) and len(links) > 0:
            for link in links:
                recipe_msg += "\n    • %s" % link

        msg += recipe_msg + "\n"

    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True

