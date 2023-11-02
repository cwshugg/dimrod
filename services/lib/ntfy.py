# A module that utilizes the 'requests' library to communicate with ntfy.sh to
# publish messages to subjects. Super useful for notifications.

# Imports
import os
import sys
import requests

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Globals
ntfy_url_base = "https://ntfy.sh"

# Sends a request to ntfy.sh to deliver a simple string message to the given
# topic.
def ntfy_send(topic: str, message: str, title=None, tags=[], priority=3):
    url = ntfy_url_base
    
    s = requests.Session()

    # build a JSON object
    jdata = {
        "topic": topic,
        "message": message,
        "priority": priority
    }
    
    # add a title, if necessary
    if title is not None:
        jdata["title"] = str(title)

    # add a tag array, if necessary
    if len(tags) > 0:
        tagstrs = [str(t) for t in tags]
        jdata["tags"] = tagstrs

    # post the request, with the message as the HTTP request body
    return s.post(url, json=jdata)

# Represents an individual ntfy.sh topic.
class NtfyChannel:
    def __init__(self, name: str):
        self.name = name
    
    # Wrapper function that invokes the above ntfy_send() function.
    def post(self, message: str, title=None, tags=[], priority=3):
        return ntfy_send(self.name, message, title=title, tags=tags, priority=priority)

