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
import jwt
import requests
from datetime import datetime

# Import(s) to run Flask in WSGI production
# https://flask.palletsprojects.com/en/2.0.x/deploying/wsgi-standalone/
from gevent.pywsgi import WSGIServer

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config


# ============================== Oracle Config =============================== #
# A config class for a generic oracle.
class OracleConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("addr",           [str],  required=True),
            lib.config.ConfigField("port",           [int],  required=True),
            lib.config.ConfigField("log",            [str],  required=False),
            lib.config.ConfigField("auth_cookie",    [str],  required=True),
            lib.config.ConfigField("auth_secret",    [str],  required=True),
            lib.config.ConfigField("auth_users",     [list], required=True),
            lib.config.ConfigField("auth_exptime",   [int],  required=False),
            lib.config.ConfigField("debug",          [bool], required=False, default=False),
            lib.config.ConfigField("https_cert",     [str],  required=False),
            lib.config.ConfigField("https_key",      [str],  required=False),
        ]


# ========================== Service Oracle Server =========================== #
# Service "oracle" thread that runs a flask server to act as a middleman
# between the user and the server. Each service I implement will not only have
# an extended 'Service' class, but also an extended 'Oracle' class as
# well.
class Oracle(threading.Thread):
    # Constructor.
    def __init__(self, config, service):
        threading.Thread.__init__(self, target=self.run)
        self.service = service
        self.config = config
        self.server = flask.Flask(__name__)

        # initialize the user objects
        self.users = []
        for udata in self.config.auth_users:
            self.users.append(User(udata))

        # initialize the optional JWT expiration time
        jwt_exptime = 2592000
        if not self.config.auth_exptime:
            self.config.auth_exptime = jwt_exptime
        else:
            self.config.auth_exptime = abs(self.config.auth_exptime)

        # make sure both certification files were given
        self.https_enabled = self.config.https_cert is not None and \
                             self.config.https_key is not None
        if self.https_enabled:
            assert os.path.isfile(self.config.https_cert), \
                   "the https_cert could not be accessed"
            assert os.path.isfile(self.config.https_key), \
                   "the https_key could not be accessed"

        # examine the config for a log stream
        log_file = sys.stdout
        if self.config.log:
            log_file = self.config.log
        log_name = self.service.config.service_name + "-oracle"
        self.log = lib.log.Log(log_name, stream=log_file)

    # Initialize the Oracle's NLA endpoints list.
    # This should be overridden by subclasses.
    def init_nla(self):
        # initialize an empty list of NLA endpoints; subclasses should add to
        # this class field
        self.nla_endpoints = []

    # Thread main function. Configures the flask server to invoke the class'
    # various handler functions, then launches it.
    def run(self):
        self.log.write("Establishing endpoints...")
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
        self.init_nla()
        self.endpoints()

        # with all endpoints and handlers set up, run the server under a WSGI
        # production server (unless debug is enabled)
        self.log.write("Launching flask...")
        addr = self.config.addr
        port = self.config.port
        if self.config.debug:
            self.server.run(addr, port=port)
        else:
            # run with our without HTTPS certification
            if self.https_enabled:
                serv = WSGIServer((addr, port), self.server, log=None,
                                  certfile=self.config.https_cert,
                                  keyfile=self.config.https_key)
            else:
                serv = WSGIServer((addr, port), self.server, log=None)
            serv.serve_forever()

    # ------------------- Server Processing and Endpoints -------------------- #
    # Function that defines a number of endpoints for the oracle. This is
    # invoked when the oracle is started, before the flask server is launched.
    def endpoints(self):
        # Default handler for '/'
        @self.server.route("/")
        def endpoint_root():
            message = "I am the oracle for %s." % self.service.config.service_name
            return self.make_response(msg=message)

        # An identification route that all service oracles have. This is used
        # to identify services by name.
        @self.server.route("/id")
        def endpoint_id():
            return self.make_response(msg=self.service.config.name)

        # An authentication endpoint used to log in and receive a JWT.
        @self.server.route("/auth/login", methods=["POST"])
        def endpoint_auth_login():
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="Missing credentials.",
                                          rstatus=400)

            # attempt to match-up the username and password
            user = self.auth_check_login(flask.g.jdata)
            if not user:
                return self.make_response(success=False,
                                          msg="Incorrect credentials.",
                                          rstatus=400)

            # create a cookie for the user
            cookie = self.auth_make_cookie(user)
            cookie_age = 999999999 if user.config.privilege == 0 else self.config.auth_exptime
            cookie_str = "%s=%s; Path=/; Max-Age=%d" % (self.config.auth_cookie, cookie, cookie_age)
            return self.make_response(msg="Authentication successful. Hello, %s." % user.config.username,
                                 rheaders={"Set-Cookie": cookie_str})

        # An authentication endpoint used to check the current log-in status.
        @self.server.route("/auth/check", methods=["GET"])
        def endpoint_auth_check():
            # the JWT-decoding step was performed in the pre-processing
            # function, so we'll just check it here
            if flask.g.user:
                return self.make_response(msg="You are authenticated as %s." % flask.g.user.config.username)
            return self.make_response(msg="You are not authenticated.", success=False)

        # Takes in message parameters and posts a message to the service's
        # message hub.
        @self.server.route("/msghub/post", methods=["POST"])
        def endpoint_msghub_post():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing/invalid message information.",
                                          success=False, rstatus=400)

            # otherwise, parse the data to understand the request
            jdata = flask.g.jdata
            if "message" not in jdata:
                return self.make_response(msg="Request must contain a \"message\".",
                                          success=False, rstatus=400)
            msg = str(jdata["message"])

            # check for optional fields
            title = None
            if "title" in jdata:
                title = str(jdata["title"])
            tags = []
            if "tags" in jdata and type(jdata["tags"]) == list:
                tags = [str(t) for t in tags]
            priority = 3
            if "priority" in jdata and type(jdata["priority"]) in [int, float]:
                priority = int(jdata["priority"])

            # send the message
            self.service.msghub.post(msg, title=title, tags=tags, priority=priority)
            return self.make_response(msg="Messaged posted successfully.")

        # Returns a list of supported NLA endpoints.
        @self.server.route("/nla/get", methods=["GET"])
        def endpoint_nla_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # build an array of JSON objects to return to the sender
            jdata = []
            for nla_ep in self.service.nla_endpoints:
                jdata.append(nla_ep.config.to_json())

            return self.make_respose(payload=jdata)

        # define all NLA endpoints
        for ep in self.nla_endpoints:
            ep_name = "endpoint_nla_%s" % ep.name
            @self.server.route(ep.get_url(), endpoint=ep_name, methods=["POST"])
            def endpoint_nla(nla_ep=ep):
                if not flask.g.user:
                    return self.make_response(rstatus=404)
                if not flask.g.jdata:
                    return self.make_response(msg="Missing input parameters.",
                                              success=False, rstatus=400)

                # invoke the handler function for this endpoint
                try:
                    nla_result = nla_ep.handler(self, flask.g.jdata)
                    return self.make_response(**nla_result)
                except Exception as e:
                    return self.make_response(msg="Error processing NLA endpoint: %s" % str(e),
                                              success=False, rstatus=400)

    # Invoked directly before a request's main handler is invoked.
    def pre_process(self):
        # parse the JSON data (if any was given)
        try:
            flask.g.jdata = self.get_request_json(flask.request)
        except:
            flask.g.jdata = None

        # attempt to decode the JWT (if present)
        flask.g.user = self.auth_check_cookie(flask.request.headers.get("Cookie"))

    # Invoked directly after a request's main handler is invoked.
    def post_process(self, response):
        # get the origin URL from the request headers to use for the
        # Access-Control-Allow-Origin response header
        origin = flask.request.headers.get("Origin")
        if not origin:
            origin = flask.request.headers.get("Host")
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Expose-Headers"] = "*, Set-Cookie"
        return response

    # Invoked to clean up resources after handling a request - even in the event
    # of an error.
    def post_process_cleanup(self, error=None):
        pass


    # ---------------------------- Authentication ---------------------------- #
    # Takes in a JSON object from an incoming request and attempts to verify a
    # login attempt. Returns the matching user object on a successful login and
    # None on a failed login.
    def auth_check_login(self, jdata):
        # extract the username and password from the JSON data
        username = "" if "username" not in jdata else jdata["username"]
        password = "" if "password" not in jdata else jdata["password"]
        if type(username) != str or type(password) != str:
            return None

        # iterate through the user list and attempt to match up the password
        for u in self.users:
            if username == u.config.username and \
               password == u.config.password:
                return u
        return None

    # Takes in a cookie from a request and attempts to verify the cookie's
    # validity. Returns None if the cookie isn't valid, or the user object
    # that corresponds to the cookie if the cookie *is* valid.
    def auth_check_cookie(self, cookie):
        if cookie == None:
            return None

        # split into individual cookies
        cookies = cookie.split(";")
        result = None
        for c in cookies:
            c = c.strip()
            # attempt to separate out the cookie value and decode it
            pieces = c.split("=")
            # check the cookie name - if this is the one we want, break out of the
            # loop and proceed
            if pieces[0] == self.config.auth_cookie:
                result = pieces[0]
                break

        # if we never found the cookie, return
        if result == None:
            return None

        # attempt to decode the cookie (don't verify the expiration time in the
        # function call - we'll do this ourself)
        try:
            result = jwt.decode(pieces[len(pieces) - 1],
                                self.config.auth_secret,
                                algorithms=["HS512"],
                                options={"verify_exp": False})
        except:
            return None

        # check for the correct fields in the decoded JWT
        if "iat" not in result or "exp" not in result or "sub" not in result:
            return None

        # check the issued-at time for the token
        now = int(datetime.now().timestamp())
        if result["iat"] > now:
            return None
        # make sure the 'sub' is one of our registered users
        user = None
        for u in self.users:
            if result["sub"] == u.config.username:
                user = u
                break
        if user == None:
            return None

        # check the expiration time for the token, but only if the user doesn't
        # have special privileges
        if user.config.privilege > 0 and result["exp"] <= now:
            return None

        # if we passed all the above checks, they must be authenticated
        return user

    # Takes in a user and generates a fresh JWT token as proof of
    # authentication.
    def auth_make_cookie(self, user):
        now = int(datetime.now().timestamp())
        data = {
            "iat": now,
            "exp": now + self.config.auth_exptime,
            "sub": user.config.username
        }
        token = jwt.encode(data, self.config.auth_secret, algorithm="HS512")
        return token

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
                success = False
                msg = "File not found."
            elif rstatus == 400:
                success = False
                msg = "Bad request."

        # construct the response JSON object
        rdata = {"success": success}
        if msg != None and msg != "":
            rdata["message"] = msg
        if len(payload) > 0 or payload == []:
            rdata["payload"] = payload

        # create the response object and set all headers
        resp = flask.Response(response=json.dumps(rdata), status=rstatus)
        resp.headers["Content-Type"] = "application/json"
        for key in rheaders:
            resp.headers[key] = rheaders[key]

        # return the response object
        return resp


# =============================== Oracle Users =============================== #
# User Config object.
class UserConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("username",      [str],      required=True),
            lib.config.ConfigField("password",      [str],      required=True),
            lib.config.ConfigField("privilege",     [int],      required=True),
        ]



# User object.
class User:
    # Constructor.
    def __init__(self, jdata):
        self.config = UserConfig()
        self.config.parse_json(jdata)

    # Returns a string representation of the object.
    def __str__(self):
        return self.config.username


# ============================== Oracle Session ============================== #
# A config class for a generic oracle session.
class OracleSessionConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("addr",           [str],     required=True),
            lib.config.ConfigField("port",           [int],     required=True),
            lib.config.ConfigField("auth_username",  [str],     required=True),
            lib.config.ConfigField("auth_password",  [str],     required=True),
        ]

# The OracleSession object serves as an interface for interacting with a
# service's oracle. This is useful for scripts, or other services, that wish to
# talk with oracles via HTTP.
class OracleSession:
    # Constructor. Takes in a config and sets up internal state.
    def __init__(self, config: OracleSessionConfig):
        self.config = config
        self.url_base = "http://%s:%d" % (self.config.addr, self.config.port)
        self.session = requests.Session()

    # Logs into the service, using the username/password provided in
    # `self.config`.
    def login(self):
        url = self.url_base + "/auth/login"
        login_data = {
            "username": self.config.auth_username,
            "password": self.config.auth_password
        }
        return self.session.post(url, json=login_data)

    # Sends a POST request.
    def post(self, endpoint: str, payload=None):
        url = self.url_base + "/" + endpoint
        return self.session.post(url, json=payload)

    # Sends a GET request.
    def get(self, endpoint: str):
        url = self.url_base + "/" + endpoint
        return self.session.get(url)

    # --------------------------- Response Parsing --------------------------- #
    # Retrieves and returns the JSON data from the response.
    @staticmethod
    def get_response_json(response):
        jdata = response.json()
        return jdata["payload"] if "payload" in jdata else jdata

    # Retrieves the 'success' field from the response's JSON data and returns
    # its value.
    @staticmethod
    def get_response_success(response):
        jdata = response.json()
        return jdata["success"]

    # Retrieves the 'message' field from the response's JSON data and returns
    # its value.
    @staticmethod
    def get_response_message(response):
        jdata = response.json()
        return jdata["message"]

