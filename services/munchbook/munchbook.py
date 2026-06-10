#!/usr/bin/python3
# This service tracks food entries for configured users. Each user has their
# own SQLite3 database of food entries, and access to each database is
# controlled by mapping oracle auth usernames to user databases.
#
# The munchbook oracle exposes endpoints for listing users, searching entries,
# and adding new entries.

# Imports
import os
import sys
import flask
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Service imports
from entry import MunchbookEntry
from db import MunchbookDatabase


# ============================== User Config ================================= #
class MunchbookUserConfig(Config):
    """Config object representing a single configured munchbook user."""
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("name",             [str],      required=True),
            ConfigField("db_path",          [str],      required=True),
            ConfigField("auth_usernames",   [list],     required=True),
        ]


# =============================== Config Class =============================== #
class MunchbookConfig(ServiceConfig):
    """Config class for the munchbook service."""
    def __init__(self):
        """Constructor."""
        super().__init__()
        fields = [
            ConfigField("users",            [list],     required=True),
        ]
        self.fields += fields


# ================================= Service ================================== #
class MunchbookService(Service):
    """Main munchbook service class."""
    def __init__(self, config_path):
        """Constructor."""
        super().__init__(config_path)
        self.config = MunchbookConfig()
        self.config.parse_file(config_path)

        # parse the user configs and create database objects for each user
        self.user_configs = []
        self.databases = {}
        for udata in self.config.users:
            uc = MunchbookUserConfig()
            uc.parse_json(udata)
            self.user_configs.append(uc)
            self.databases[uc.name] = MunchbookDatabase(uc.db_path)

    def run(self):
        """Overridden main function implementation."""
        super().run()

    def get_databases_for_auth_user(self, auth_username: str):
        """Returns a list of (user_name, MunchbookDatabase) tuples for all
        databases the given auth username has access to.
        """
        result = []
        for uc in self.user_configs:
            if auth_username in uc.auth_usernames:
                result.append((uc.name, self.databases[uc.name]))
        return result

    def get_database(self, user_name: str):
        """Returns the MunchbookDatabase for a given user name, or None if
        the user does not exist.
        """
        return self.databases.get(user_name, None)

    def check_access(self, auth_username: str, user_name: str):
        """Returns True if the given auth username has access to the given
        user's database, False otherwise.
        """
        for uc in self.user_configs:
            if uc.name == user_name:
                return auth_username in uc.auth_usernames
        return False


# ============================== Service Oracle ============================== #
class MunchbookOracle(Oracle):
    """Oracle for the munchbook service, exposing HTTP endpoints for managing
    food entries.
    """
    def endpoints(self):
        """Endpoint definition function."""
        super().endpoints()

        # Endpoint that returns a list of users the authenticated user has
        # access to.
        @self.server.route("/users/list", methods=["GET"])
        def endpoint_users_list():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # get the authenticated user's username and find accessible
            # databases
            username = flask.g.user.config.username
            dbs = self.service.get_databases_for_auth_user(username)

            # build the payload
            payload = []
            for (name, db) in dbs:
                payload.append({
                    "name": name,
                })
            return self.make_response(success=True, payload=payload)

        # Endpoint that searches a user's database for entries within a
        # given time range.
        @self.server.route("/entries/search", methods=["POST"])
        def endpoint_entries_search():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # validate required JSON fields
            jdata = flask.g.jdata
            for field in ["user_name", "start", "end"]:
                if field not in jdata:
                    return self.make_response(
                        msg="Must specify '%s' field." % field,
                        success=False, rstatus=400)

            user_name = str(jdata["user_name"])
            start = jdata["start"]
            end = jdata["end"]

            # validate types
            if type(start) != int or type(end) != int:
                return self.make_response(
                    msg="'start' and 'end' must be integers.",
                    success=False, rstatus=400)

            # validate range
            if start > end:
                return self.make_response(
                    msg="'start' must be less than or equal to 'end'.",
                    success=False, rstatus=400)

            # check access
            username = flask.g.user.config.username
            if not self.service.check_access(username, user_name):
                return self.make_response(
                    msg="Access denied.", success=False, rstatus=403)

            # get the database and search
            db = self.service.get_database(user_name)
            if db is None:
                return self.make_response(
                    msg="User not found.", success=False, rstatus=404)

            # parse optional count parameter
            count = jdata.get("count", None)
            if count is not None and type(count) != int:
                return self.make_response(
                    msg="'count' must be an integer.",
                    success=False, rstatus=400)

            results = db.search_by_time_range_ts(start, end, count=count)

            # build the payload
            payload = []
            for e in results:
                payload.append(e.to_json(include_id=True))
            return self.make_response(success=True, payload=payload)

        # Endpoint that adds a new munchbook entry to a user's database.
        @self.server.route("/entries/add", methods=["POST"])
        def endpoint_entries_add():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            jdata = flask.g.jdata

            # validate required fields
            if "user_name" not in jdata:
                return self.make_response(
                    msg="Must specify 'user_name' field.",
                    success=False, rstatus=400)

            user_name = str(jdata["user_name"])

            # check access
            username = flask.g.user.config.username
            if not self.service.check_access(username, user_name):
                return self.make_response(
                    msg="Access denied.", success=False, rstatus=403)

            # get the database
            db = self.service.get_database(user_name)
            if db is None:
                return self.make_response(
                    msg="User not found.", success=False, rstatus=404)

            # attempt to construct a MunchbookEntry from the JSON and add it
            # to the database
            try:
                entry = MunchbookEntry()
                entry.parse_json(jdata)
                db.add(entry)
                return self.make_response(msg="Added successfully.",
                                          success=True,
                                          payload={"entry_id": entry.get_id()})
            except Exception as e:
                return self.make_response(
                    msg="Invalid entry data: %s" % e,
                    success=False, rstatus=400)


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=MunchbookConfig, service=MunchbookService,
                     oracle=MunchbookOracle)
    cli.run()
