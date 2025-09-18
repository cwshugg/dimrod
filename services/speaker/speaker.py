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
from lib.dialogue.dialogue import DialogueConfig, DialogueInterface, \
                                  DialogueAuthor, DialogueAuthorType, \
                                  DialogueConversation, DialogueMessage


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

    # Takes in a message and attempts to find (and invoke) one or more NLA
    # endpoints across the various configured services.
    # Returns a list of NLAResults for each action that was executed.
    def nla_process(self, message: str):
        results = []

        self.log.write("TODO - NLA ENDPOINTS")

        # ping all configured services to see if they have any NLA endpoints
        # TODO

        # for each endpoint, determine if the message should be sent to that
        # NLA endpoint
        # TODO
        endpoints = []

        # for each endpoint that should be invoked, invoke it and collect the
        # result
        for ep in endpoints:
            self.log.write("Invoking NLA endpoint: %s" % ep.get_url()) # TODO - also print service name that is being invoked
            # TODO
            pass

        return results


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
            # ---------- TODO - IMPLEMENT NEW AI-BASED ACTIONS ---------- #
            responses = self.service.nla_process(msg)
            responses = None
            # ---------- TODO - IMPLEMENT NEW AI-BASED ACTIONS ---------- #
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

        # This endpoint is used to complete a single, "oneshot" chat completion.
        @self.server.route("/oneshot", methods=["POST"])
        def endpoint_oneshot():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # look for the message field
            if "message" not in flask.g.jdata:
                return self.make_response(msg="Missing message.",
                                          success=False, rstatus=400)
            msg = str(flask.g.jdata["message"])

            # look for the intro message field
            if "intro" not in flask.g.jdata:
                return self.make_response(msg="Missing intro/system message.",
                                          success=False, rstatus=400)
            intro = str(flask.g.jdata["intro"])

            # pass the message and the intro to the dialogue object's
            # `oneshot()` function
            answer = None
            try:
                answer = self.service.dialogue.oneshot(intro, msg)
            except Exception as e:
                self.log.write("Failed to process dialogue oneshot: %s" % e)
                return self.make_response(msg="Failed to process dialogue oneshot.",
                                          success=False, rstatus=400)

            # pack the response into a JSON object and send it back
            rdata = {"message": answer}
            return self.make_response(payload=rdata)

        # This endpoint is used to have DImROD rephrase a given sentence.
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

