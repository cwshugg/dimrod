# Main Flask server module that all of my homemade services will use to
# communicate status and other information.
#
#   Connor Shugg

# Imports
from flask import Flask, Response

# Globals
server = Flask(__name__)
service = None

# ================================= Helpers ================================== #
# Takes in the HTTP request object and parses out any JSON data in the message
# body. Returns None, a dictionary, or throws an exception.
def get_request_json(request):
    raw = request.get_data()
    if len(rdata) == 0:
        return None
    return json.loads(rdata.decode())

# Used to construct a JSON object to be sent in a response message.
def make_response(success=True, msg=None, jdata={}, rstatus=200, rheaders={}):
    # update the message if necessary
    if msg == None or msg == "":
        if status == 404:
            msg = "File not found."
        elif rstatus == 400:
            msg = "Bad request."

    # construct the response JSON object (any given 'jdata' becomes out payload)
    rdata = {"success": success, "message": msg}
    if len(jdata) > 0:
        rdata["payload"] = jdata

    # create the response object and set all headers
    resp = Response(response=json.dumps(rdata), status=rstatus)
    resp.headers["Content-Type"] = "application/json"
    for key in rheaders:
        resp.headers[key] = rheaders[key]

    # return the response object
    return resp


# ========================== Request Pre-Processing ========================== #
# Invoked before the first request is received. Useful for initialization.
@server.before_first_request
def server_init():
    global service
    service = server.config["service"]
    pass

# Invoked before each request is passed to the correct handler function.
# Useful for any necessary pre-processing.
@server.before_request
def pre_process():
    # TODO
    pass


# ========================= Request Post-Processing ========================== #
# Invoked after each request is handled. Useful for any necessary
# post-processing.
@server.after_request
def post_process(response):
    # TODO
    pass

# Typically invoked during an exception in a handler function. Useful for extra
# error handling.
@server.teardown_request
def post_process_error(error=None):
    # TODO
    pass


# ================================ Endpoints ================================= #
# Home '/' endpoint.
@server.route("/")
def endpoint_root():
    return make_response(msg="I am awake.")

# Endpoint used to collect a generic status report of the service.
@server.route("/status")
def endpoint_status():
    return make_response(msg="TODO - STATUS")

