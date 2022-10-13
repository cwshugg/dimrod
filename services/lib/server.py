# Main Flask server module that all of my homemade services will use to
# communicate status and other information.
#
#   Connor Shugg

# Imports
from flask import Flask, Response


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

