# Implements the /list bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

from lib.oracle import OracleSession

# Sends a summary of all lists.
def summarize(service, message, args, session, lists):
    msg = "<b>All Lists</b>\n\n"
    for l in lists:
        length = len(l["tasks"])
        msg += "• <b>%s</b> - %d item%s\n" % \
               (l["name"], length, "s" if length != 1 else "")
    service.send_message(message.chat.id, msg, parse_mode="HTML")

# Prints one list.
def show(service, message, args, session, lists):
    # make sure a name was provided
    if len(args) < 2:
        service.send_message(message.chat.id,
                             "You need to specify a name.")
        return
    name = args[1].strip().lower()
    
    # find the list by using each arg as a word to match
    words = args[1:]
    lst = None
    for l in lists:
        lname = l["name"].strip().lower()
        matches = 0
        for w in words:
            if w.strip().lower() in lname:
                matches += 1
        # if all words matched, we found our list
        if matches == len(words):
            lst = l
            break

    if lst is None:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't find a list with those "
                             "keywords in the name.")
        return
    
    # craft and send a message
    msg = "<b>%s</b>\n" % l["name"]
    count = 1
    for item in lst["tasks"]:
        msg += "\n<b>[%d]</b> %s\n" % (count, item["title"])
        if len(item["content"]) > 0:
            clines = item["content"].split("\n")
            for cl in clines:
                msg += "• %s\n" % cl
        count += 1
    service.send_message(message.chat.id, msg, parse_mode="HTML")

# Adds to a list.
def add(service, message, args, session, lists):
    # make sure a name was provided
    if len(args) < 3:
        service.send_message(message.chat.id,
                             "You need to specify a list name.")
        return
    name = args[2].strip().lower()

    # make sure other arguments were specified (this will be used as the
    # list item's text)
    if len(args) < 4:
        service.send_message(message.chat.id,
                             "You need to specify what the new list item is.")
        return
    item = " ".join(args[3:])

    # request the adding of the new item
    try:
        r = session.post("/list/append", payload={"name": name, "item": item})
        service.send_message(message.chat.id,
                             session.get_response_message(r))
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't add to the list (%s)" % e)

# Removes from a list.
def remove(service, message, args, session, lists):
    # make sure a name was provided
    if len(args) < 3:
        service.send_message(message.chat.id,
                             "You need to specify a list name.")
        return
    name = args[2].strip().lower()

    # make sure other arguments were specified (this will be used as the
    # text to search for a list item to delete)
    if len(args) < 4:
        service.send_message(message.chat.id,
                             "You need to specify the text of the list item to remove.")
        return
    item = " ".join(args[3:]).lower()

    # first, find the matching list
    target_list = None
    for l in lists:
        if name == l["name"]:
            target_list = l
            break
    if target_list is None:
        service.send_message(message.chat.id,
                             "I couldn't find a list named \"%s\"." % name)
        return

    # iterate through the list and search for item
    iid = None
    for i in target_list["items"]:
        if item in i["text"].lower():
            iid = i["iid"]
            break
    if iid is None:
        service.send_message(message.chat.id,
                             "I couldn't find a matching list item.")
        return

    # request the removal of the item
    try:
        r = session.post("/list/remove", payload={"name": name, "iid": iid})
        service.send_message(message.chat.id,
                             session.get_response_message(r))
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't remove from the list (%s)" % e)

# Main function.
def command_list(service, message, args: list):
    # create a session with scribble
    session = OracleSession(service.config.scribble_address,
                            service.config.scribble_port)
    try:
        r = session.login(service.config.scribble_auth_username,
                          service.config.scribble_auth_password)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Scribble. "
                             "It might be offline.")
        return False

    # retrieve all lists from scribble
    lists = []
    try:
        r = session.get("/list/get/all")
        lists = session.get_response_json(r)
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve list data. (%s)" % e)

    # if no extra arguments were given, list all the available lists
    if len(args) == 1:
        return summarize(service, message, args, session, lists)

    # look for sub-commands
    subcmd = args[1].strip().lower()
    if subcmd in ["add", "append"]:
        return add(service, message, args, session, lists)
    elif subcmd in ["remove", "cut"]:
        return remove(service, message, args, session, lists)

    # if no subcommand was found, interpret it as a list name
    return show(service, message, args, session, lists)

