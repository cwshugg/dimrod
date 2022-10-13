# This module defines the overarching Service class, that encompasses the Flask
# server. Each service has at least two threads:
#   1. One thread to run the Flask server.
#   2. Another thread to run the main service code.
#
#   Connor Shugg

# Imports
import os
import sys
import threading
import json
import flask

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config


# ================================ Main Class ================================ #
# Main service class. Caller must 'start()' the service as if it was starting a
# thread. In addition, the caller must handle the function invocation to spawn
# the service's oracle (server) thread.
# Each service has a 'bank' that's used to store values that can be written to
# or read from by other threads synchronously.
class Service(threading.Thread):
    # Constructor.
    def __init__(self, config_path):
        threading.Thread.__init__(self, target=self.run)
        self.config = lib.config.Config(config_path)
        self.oracle = Oracle(self)
        # set up synchronization fields
        self.lock = threading.Lock()
        # set up logging fields
        self.log_stream = sys.stdout
    
    # The service's main thread. This function must is where all the service's
    # actual work will occur, and thus must be extended by subclasses.
    def run(self):
        self.log("spawned.")

    # Takes in a message and writes it to the log's output, with a prefix.
    # Optionally takes in a 'stream' variable that accepts an optional file
    # pointer. Each time 'stream' is specified, an internal field is updated.
    # So, it only needs to be specified once to write to the same file stream
    # every time.
    def log(self, msg, stream=sys.stdout):
        self.log_stream = stream
        tid = threading.get_ident()
        prefix = "[%s-%d] " % (self.config.name, tid)
        self.log_stream.write("%s%s\n" % (prefix, msg))


# ============================== Server Thread =============================== #
# Service "oracle" thread that runs a flask server to act as a middleman
# between the user and the server. Each service I implement will not only have
# an extended 'Service' class, but also an extended 'Oracle' class as
# well.
class Oracle(threading.Thread):
    # Constructor.
    def __init__(self, service):
        threading.Thread.__init__(self, target=self.run)
        self.service = service
        self.server = flask.Flask(__name__)

    # Function that defines a number of endpoints for the oracle. This is
    # invoked when the oracle is started, before the flask server is launched.
    def endpoints(self):
        # Default handler for '/'
        @self.server.route("/")
        def endpoint_root():
            return self.make_response(msg="I am alive.")

    # Thread main function. Configures the flask server to invoke the class'
    # various handler functions, then launches it.
    def run(self):
        # PRE-PROCESSING
        @self.server.before_request
        def pre_process():
            return self.pre_process()
        
        # POST-PROCESSING
        @self.server.after_request
        def post_process(response):
            return self.post_process(response)
        
        # POST-REQUEST CLEANUP
        @self.server.teardown_request
        def post_process_cleanup(error=None):
            return self.post_process_cleanup(error=error)

        # ENDPOINT REGISTRATION
        self.endpoints()

        # with all endpoints and handlers set up, run the server
        addr = self.service.config.server_addr
        port = self.service.config.server_port
        self.server.run(addr, port=port)
        
    # ---------------------- Server Pre/Post-Processing ---------------------- #
    # Invoked directly before a request's main handler is invoked.
    def pre_process(self):
        pass
    
    # Invoked directly after a request's main handler is invoked.
    def post_process(self, response):
        return response

    # Invoked to clean up resources after handling a request - even in the event
    # of an error.
    def post_process_cleanup(self, error=None):
        pass

    # ------------------------------- Helpers -------------------------------- #
    # Takes in the HTTP request object and parses out any JSON data in the
    # message body. Returns None, a dictionary, or throws an exception.
    def get_request_json(self, request):
        raw = request.get_data()
        if len(rdata) == 0:
            return None
        return json.loads(rdata.decode())
    
    # Used to construct a JSON object to be sent in a response message.
    def make_response(self, success=True, msg=None, jdata={}, rstatus=200, rheaders={}):
        # update the message if necessary
        if msg == None or msg == "":
            if status == 404:
                msg = "File not found."
            elif rstatus == 400:
                msg = "Bad request."
    
        # construct the response JSON object (any given 'jdata' becomes out
        # payload)
        rdata = {"success": success, "message": msg}
        if len(jdata) > 0:
            rdata["payload"] = jdata
    
        # create the response object and set all headers
        resp = flask.Response(response=json.dumps(rdata), status=rstatus)
        resp.headers["Content-Type"] = "application/json"
        for key in rheaders:
            resp.headers[key] = rheaders[key]
    
        # return the response object
        return resp

