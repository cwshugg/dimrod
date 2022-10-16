# This module defines the 'Oracle' thread for a service. The oracle serves as a
# middleman between the actual service and the user.
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


# ============================== Service Config ============================== #
# A config class for a generic oracle.
class OracleConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("oracle_addr",   [str],      required=True),
            lib.config.ConfigField("oracle_port",   [int],      required=True),
            lib.config.ConfigField("oracle_log",    [str],      required=False),
        ]


# ========================== Service Oracle Server =========================== #
# Service "oracle" thread that runs a flask server to act as a middleman
# between the user and the server. Each service I implement will not only have
# an extended 'Service' class, but also an extended 'Oracle' class as
# well.
class Oracle(threading.Thread):
    # Constructor.
    def __init__(self, config_path, service):
        threading.Thread.__init__(self, target=self.run)
        self.service = service
        self.config = OracleConfig()
        self.config.parse_file(config_path)
        self.server = flask.Flask(__name__)
        
        # examine the config for a log stream
        log_file = sys.stdout
        if self.config.oracle_log:
            log_file = self.config.oracle_log
        log_name = self.service.config.service_name + "-oracle"
        self.log = lib.log.Log(log_name, stream=log_file)
        
    # Thread main function. Configures the flask server to invoke the class'
    # various handler functions, then launches it.
    def run(self):
        self.log.write("establishing endpoints...")
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
        self.log.write("launching flask...")
        addr = self.config.oracle_addr
        port = self.config.oracle_port
        self.server.run(addr, port=port)
        
    # ------------------- Server Processing and Endpoints -------------------- #
    # Function that defines a number of endpoints for the oracle. This is
    # invoked when the oracle is started, before the flask server is launched.
    def endpoints(self):
        # Default handler for '/'
        @self.server.route("/")
        def endpoint_root():
            message = "I am the oracle for %s." % self.service.config.name
            return self.make_response(msg=message)
        
        # An identification route that all service oracles have. This is used
        # to identify services by name.
        @self.server.route("/id")
        def endpoint_id():
            return self.make_response(msg=self.service.config.name)

    # Invoked directly before a request's main handler is invoked.
    def pre_process(self):
        # parse the JSON data (if any was given)
        try:
            flask.g.jdata = self.get_request_json(flask.request)
        except:
            flask.g.jdata = None
    
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
        if len(raw) == 0:
            return {}
        return json.loads(raw.decode())
    
    # Used to construct a JSON object to be sent in a response message.
    def make_response(self, success=True, msg=None, payload={}, rstatus=200, rheaders={}):
        # update the message if necessary
        if msg == None or msg == "":
            if rstatus == 404:
                msg = "File not found."
            elif rstatus == 400:
                msg = "Bad request."
    
        # construct the response JSON object
        rdata = {"success": success}
        if msg != None and msg != "":
            rdata["message"] = msg
        if len(payload) > 0:
            rdata["payload"] = payload
    
        # create the response object and set all headers
        resp = flask.Response(response=json.dumps(rdata), status=rstatus)
        resp.headers["Content-Type"] = "application/json"
        for key in rheaders:
            resp.headers[key] = rheaders[key]
    
        # return the response object
        return resp

