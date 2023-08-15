#!/usr/bin/python3
# This service acts as a wrapper around the dialogue library code I've written.
# It exposes a dialogue interface with DImROD to the network. (The service
# itself doesn't do anything; the oracle is what makes the dialogue available.)

# Imports
import os
import sys
import flask
import subprocess
import time
import hashlib
from datetime import datetime
import inspect
import importlib.util

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface, DialogueAuthor, \
                         DialogueAuthorType, DialogueConversation, DialogueMessage

# Speaker imports
from action import *


# =============================== Config Class =============================== #
class SpeakerConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("speaker_tick_rate",        [int],      required=False,     default=30),
            ConfigField("speaker_mood_timeout",     [int],      required=False,     default=1200),
            ConfigField("speaker_actions",          [list],     required=False,     default=[])
        ]


# ============================== Service Class =============================== #
class SpeakerService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = SpeakerConfig()
        self.config.parse_file(config_path)

        # create a dialogue config object and a dialogue interface object
        self.dialogue_conf = DialogueConfig()
        self.dialogue_conf.parse_file(config_path)
        self.dialogue = DialogueInterface(self.dialogue_conf)

        # action-related class fields
        self.actions = None
        self.action_classes = None

    # Overridden main function implementation.
    def run(self):
        super().run()
        
        self.remood()
        while True:
            # periodically, choose a new mood for DImROD's dialogue library to use
            # for building responses
            now = datetime.now()
            if now.timestamp() - self.mood_timestamp.timestamp() >= self.config.speaker_mood_timeout:
                self.remood()
            
            # sleep before re-looping
            time.sleep(self.config.speaker_tick_rate)
    
    # Sets a new mood in the Dialogue library.
    def remood(self, new_mood=None):
        mood = self.dialogue.remood(new_mood=new_mood)
        self.mood_timestamp = datetime.now()
        self.log.write("Setting dialogue mood to: \"%s\"" % mood.name)

    # ------------------------------- Actions -------------------------------- #
    # Imports all available action classes and reads from the config to build
    # and return a list of DialogueAction classes.
    def actions_load(self):
        # only do this once per execution of the speaker
        if self.actions is not None:
            return self.actions

        # make sure the actions directory exists
        actions_dir = os.path.join(os.path.dirname(__file__), "actions")
        assert os.path.isdir(actions_dir), "missing actions directory: %s" % actions_dir

        # search the actions directory for python files
        self.action_classes = {}
        for (root, dirs, files) in os.walk(actions_dir):
            for f in files:
                if f.lower().endswith(".py"):
                    # import the file
                    mpath = "actions.%s" % f.replace(".py", "")
                    mod = importlib.import_module(mpath)

                    # inspect the module's members
                    for (name, cls) in inspect.getmembers(mod, inspect.isclass):
                        # ignore the base class - append everything else that's
                        # a child of the "base" class
                        if issubclass(cls, DialogueAction):
                            self.action_classes[name] = cls
        
        # with the classes loaded in, examine the config field and use the data
        # to build a list of dialogue action objects (which is what we'll use
        # for action intent parsing)
        self.actions = []
        for entry in self.config.speaker_actions:
            # search for the appropriate class to build. If it isn't known, skip
            # this entry
            cname = entry["class_name"]
            if cname not in self.action_classes:
                self.log.write("Unrecognized action class name: \"%s\". Skipping." % cname)
                continue

            # take the class and construct an object, then build its parsing
            # engine
            aclass = self.action_classes[cname]
            a = aclass(entry)
            a.engine_init()
            self.actions.append(a)
            self.log.write("Loaded action class: \"%s\"" % cname)

        # return the list of actions
        return self.actions
    
    # Takes in a message and attempts to parse it via one of the configured
    # actions. Returns depending on if an action was carried out or not.
    def actions_process(self, message: str):
        acts = self.actions_load()

        # for each action, attempt to parse intent (each may produce a response
        # message)
        responses = []
        for a in acts:
            result = a.engine_process(message)
            if result is not None:
                self.log.write("Found intent within message to fire %s." % type(a).__name__)
            # append each response in the result (the return value could be
            # a single message or a list of messages)
            if type(result) == str:
                responses.append(result)
            elif type(result) == list:
                for msg in result:
                    responses.append(msg)
            elif result is not None:
                responses.append("I executed the %s routine." % type(a).__name__)

        # if responses were generated, return them (otherwise return None)
        return None if len(responses) == 0 else responses
    

# ============================== Service Oracle ============================== #
class SpeakerOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # This endpoint is used to talk with DImROD. A message is passed, along
        # with other optional metadata, and DImROD's response is returned.
        @self.server.route("/talk", methods=["POST"])
        def endpoint_talk():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # look for the message field
            if "message" not in flask.g.jdata:
                return self.make_response(msg="Missing message.",
                                          success=False, rstatus=400)
            msg = str(flask.g.jdata["message"])

            # look for the conversation ID (optional)
            cid = None
            convo = None
            if "conversation_id" in flask.g.jdata:
                cid = str(flask.g.jdata["conversation_id"])
                # search for a matching conversation
                result = self.service.dialogue.search_conversation(cid=cid)
                if len(result) == 0:
                    return self.make_response(msg="Unknown conversation ID.",
                                              success=False, rstatus=400)
                convo = result[0]

            # look for an optional author name
            aname = None
            if "author_name" in flask.g.jdata:
                aname = str(flask.g.jdata["author_name"])

            # look for an optional author ID
            author = None
            if "author_id" in flask.g.jdata:
                aid = str(flask.g.jdata["author_id"])
                result = self.service.dialogue.search_author(aid=aid)
                if len(result) == 0:
                    return self.make_response(msg="Unknown author ID.",
                                              success=False, rstatus=400)
                author = result[0]

            # if an author ID wasn't given, we'll create a new one
            if author is None:
                # select or create an author name
                name = aname
                if name is None:
                    salt = "%s-%s" % (str(aname), str(datetime.now().timestamp()))
                    salt = salt.encode("utf-8") + os.urandom(8)
                    name = "ORACLE_USER_%s" % hashlib.sha256(salt).hexdigest()
                author = DialogueAuthor(name, DialogueAuthorType.USER_ORACLE)

            # before passing anything to the dialogue library, try to parse the
            # text as a call to action. If successful, an array of messages will
            # be returned.
            responses = self.service.actions_process(msg)
            if responses is not None:
                # build a comprehensive response message to send back,
                # containing all the reported response messages from the
                # individual actions carried out
                resp = "I executed some routines."
                if len(responses) == 1:
                    resp = responses[0]
                elif len(responses) > 1:
                    resp = "I executed some routines:\n"
                    for response in responses:
                        resp += "%s\n" % response

                # attempt to have the dialogue service reword the message
                # to add some variance. On failure, send the original
                # message
                try:
                    resp = self.service.dialogue.reword(resp)
                except Exception as e:
                    self.log.write("Failed to reword action responses: %s" % e)

                # send the response message back to the caller
                self.log.write("Completed actions and sent back %d responses." % len(responses))
                return self.make_response(payload={"response": resp})

            # send the message to the dialogue interface
            try:
                convo = self.service.dialogue.talk(msg, conversation=convo, author=author)
            except Exception as e:
                return self.make_response(msg="Failed to converse: %s" % e,
                                          success=False)

            # save the author and the conversation
            self.service.dialogue.save_author(author)
            self.service.dialogue.save_conversation(convo)

            # build a response object containing the response message,
            # conversation ID, author info, etc.
            rdata = {
                "conversation_id": convo.get_id(),
                "author_id": author.get_id(),
                "response": convo.latest_response().content
            }
            return self.make_response(payload=rdata)

        # This endpoint is used to have DImROD rephrease a given sentence.
        @self.server.route("/reword", methods=["POST"])
        def endpoint_reword():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # look for the message field
            if "message" not in flask.g.jdata:
                return self.make_response(msg="Missing message.",
                                          success=False, rstatus=400)
            msg = str(flask.g.jdata["message"])

            # pass the message to the 'reword' function and receive a response
            rewording = None
            try:
                rewording = self.service.dialogue.reword(msg)
            except Exception as e:
                self.log.write("Failed to reword phrase: %s" % e)
                return self.make_response(msg="Failed to reword the phrase.",
                                          success=False, rstatus=400)

            # pack the response into a JSON object and send it back
            rdata = {"message": rewording}
            return self.make_response(payload=rdata)

        
# =============================== Runner Code ================================ #
cli = ServiceCLI(config=SpeakerConfig, service=SpeakerService, oracle=SpeakerOracle)
cli.run()

