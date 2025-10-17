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
import json
import copy
import threading
import enum

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface, \
                         DialogueAuthor, DialogueAuthorType, \
                         DialogueConversation, DialogueMessage
from lib.nla import NLAService, NLAEndpoint, NLAResult, NLAEndpointInvokeParameters


# =============================== Config Class =============================== #
class SpeakerConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("dialogue",         [DialogueConfig], required=True),
            ConfigField("tick_rate",        [int],      required=False,     default=30),
            ConfigField("mood_timeout",     [int],      required=False,     default=1200),
            ConfigField("nla_services",     [NLAService], required=False,   default=[]),
            ConfigField("nla_dialogue_retry_count", [int], required=False,  default=5),
            ConfigField("nla_threads",      [int],      required=False,     default=8),
        ]


# ============================== NLA Threading =============================== #
# An enum representing the different states a NLA queue entry can be in.
class SpeakerNLAQueueEntryStatus(enum.Enum):
    PENDING = 0
    PROCESSING = 1
    SUCCESS = 2
    FAILURE = 3

# An object used to store NLA information pushed to the queue, as well as store
# the entry's status and result.
class SpeakerNLAQueueEntry:
    def __init__(self, nla_info: dict):
        self.info = nla_info
        self.status = SpeakerNLAQueueEntryStatus.PENDING
        self.result = None

        # give the entry a lock and a condition variable, so the caller can
        # wait for this entry to be complete
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)

    def update(self, status: SpeakerNLAQueueEntryStatus, result=None):
        self.lock.acquire()
        self.status = status
        if result is not None:
            self.result = result
        self.cond.notify()
        self.lock.release()

    def wait(self):
        self.lock.acquire()
        # wait as long as the entry is still sitting in the queue, or it's
        # being currently processed by a thread
        while self.status in [SpeakerNLAQueueEntryStatus.PENDING,
                              SpeakerNLAQueueEntryStatus.PROCESSING]:
            self.cond.wait()
        self.lock.release()
        return self.result

# Represents a queue used to submit NLA endpoint actions to the NLA threads.
class SpeakerNLAQueue:
    # Constructor.
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)
        self.queue = []

    # Pushes to the queue and alerts a waiting thread.
    def push(self, data: dict):
        self.lock.acquire()
        entry = SpeakerNLAQueueEntry(data)
        self.queue.append(entry)
        self.cond.notify()
        self.lock.release()

        return entry

    # Pops from the queue, blocking if the queue is empty.
    def pop(self):
        self.lock.acquire()
        while len(self.queue) == 0:
            self.cond.wait()
        entry = self.queue.pop(0)
        self.lock.release()
        return entry

# Represents an individual thread used to handle NLA endpoint invocations.
# A pool of these threads will be created by the speaker service, and will
# repeatedly pull from the queue to execute NLA endpoint invocations.
class SpeakerNLAThread(threading.Thread):
    # Constructor
    def __init__(self, service, queue: SpeakerNLAQueue):
        super().__init__(target=self.run)
        self.service = service
        self.queue = queue

    # Writes a log message using the speaker service's log object.
    def log(self, msg: str):
        ct = threading.current_thread()
        self.service.log.write("[NLA Thread %d] %s" % (ct.native_id, msg))

    # The thread's main function.
    def run(self):
        self.log("Spawned.")

        # loop forever
        while True:
            # pop from the queue (this will block if the queue is empty)
            entry = self.queue.pop()

            # update the entry's status to indicate it's being processed
            entry.update(SpeakerNLAQueueEntryStatus.PROCESSING)

            # grab a few fields within the popped queue object
            ep_id = entry.info["id"]
            ep = entry.info["endpoint"]
            service = entry.info["service"]

            self.log("Invoking NLA endpoint \"%s\" with params: %s" % (ep_id, str(entry.info["invoke_params"])))

            # open a session to the service that owns this endpoint, and
            # attempt to log in
            session = OracleSession(service.oracle)
            try:
                lr = session.login()
                if OracleSession.get_response_status(lr) != 200:
                    self.log("Failed to log into service \"%s\". "
                             "Skipping this endpoint. (%s)" %
                             (service.name, str(lr)))
                    entry.update(SpeakerNLAQueueEntryStatus.FAILURE)
                    continue
            except Exception as e:
                    self.log("Failed to log into service \"%s\". "
                             "Skipping this endpoint. (%s)" %
                             (service.name, str(e)))
                    entry.update(SpeakerNLAQueueEntryStatus.FAILURE)
                    continue

            # post to the endpoint's URL; provide the message as the payload
            try:
                r = session.post(ep.get_url(), payload=entry.info["invoke_params"].to_json())

                # make sure the invocation succeeded
                if OracleSession.get_response_status(r) != 200:
                    self.log("Failed to invoke NLA endpoint \"%s\": %s" %
                             (ep_id, str(r)))
                    entry.update(SpeakerNLAQueueEntryStatus.FAILURE)
                    continue

                # upon a successful call to the NLA endpoint, save the result
                # to the queue entry object, and update its status to indicate
                # a successful invocation
                result = NLAResult.from_json(OracleSession.get_response_json(r))
                entry.update(SpeakerNLAQueueEntryStatus.SUCCESS, result=result)
            except Exception as e:
                self.log("Failed to invoke NLA endpoint \"%s\": %s" %
                         (ep_id, str(e)))
                entry.update(SpeakerNLAQueueEntryStatus.FAILURE)
                continue

# ============================== Service Class =============================== #
class SpeakerService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = SpeakerConfig()
        self.config.parse_file(config_path)

        # create a dialogue config object and a dialogue interface object
        self.dialogue = DialogueInterface(self.config.dialogue)

        # action-related class fields
        self.actions = None
        self.action_classes = None

        # create the NLA thread queue, and spawn NLA threads
        self.nla_queue = SpeakerNLAQueue()
        self.nla_threads = []
        for i in range(self.config.nla_threads):
            t = SpeakerNLAThread(self, self.nla_queue)
            t.start()
            self.nla_threads.append(t)


    # Overridden main function implementation.
    def run(self):
        super().run()

        self.remood()
        while True:
            # periodically, choose a new mood for DImROD's dialogue library to use
            # for building responses
            now = datetime.now()
            if now.timestamp() - self.mood_timestamp.timestamp() >= self.config.mood_timeout:
                self.remood()

            # sleep before re-looping
            time.sleep(self.config.tick_rate)

    # Sets a new mood in the Dialogue library.
    def remood(self, new_mood=None):
        mood = self.dialogue.remood(new_mood=new_mood)
        self.mood_timestamp = datetime.now()
        self.log.write("Setting dialogue mood to: \"%s\"" % mood.name)

    # Takes in a message and attempts to find (and invoke) one or more NLA
    # endpoints across the various configured services.
    # Returns a list of NLAResults for each action that was executed.
    def nla_process(self, message: str, request_data: dict):
        results = []

        # the loop below will construct a dictionary of endpoints to search
        # through. Each endpoint will have a unique ID string to use as the key
        endpoints = {}

        # ping all configured services to see if they have any NLA endpoints
        for nla_service in self.config.nla_services:
            session = OracleSession(nla_service.oracle)

            # log into the service; if it fails, skip this one
            try:
                lr = session.login()
                if OracleSession.get_response_status(lr) != 200:
                    self.log.write("Failed to log into service \"%s\": %s" % (nla_service.name, str(lr)))
                    continue
            except Exception as e:
                    self.log.write("Failed to log into service \"%s\": %s" % (nla_service.name, str(e)))
                    continue

            # retrieve NLA endpoints from the service; skip if it fails
            r = session.get("/nla/get")
            if OracleSession.get_response_status(r) != 200:
                self.log.write("Failed to get NLA endpoints from service \"%s\": %s" % (nla_service.name, str(r)))
                continue

            # reconstruct the NLAEndpoint objects from the response
            rdata = OracleSession.get_response_json(r)
            service_id = nla_service.name.lower().replace(" ", "_")
            for entry in rdata:
                try:
                    ep = NLAEndpoint.from_json(entry)
                    ep_id = "%s::%s" % (service_id, ep.get_url())

                    # there should be no overlaps in endpoint IDs; if there is,
                    # log an error and skip this one
                    if ep_id in endpoints:
                        self.log.write("Duplicate NLA endpoint ID detected: %s" % ep_id)
                        continue

                    # otherwise, add the endpoint, using its ID as the key, to
                    # the endpoint dictionary
                    ep_data = {
                        "id": ep_id,
                        "service": nla_service,
                        "endpoint": ep
                    }
                    endpoints[ep_id] = ep_data
                except Exception as e:
                    self.log.write("Failed to parse NLA endpoint (from service \"%s\") from JSON: \"%s\" - %s" %
                                   (nla_service.name, str(entry), e))
                    continue

        # build a prompt that we'll use to ask an LLM which API endpoints
        # should be invoked
        prompt_intro = "You are an AI assistant that processes messages written by the user, " \
                        "and determines if a specific API endpoint should be invoked.\n" \
                        "Below is a list of the API endpoints in question:\n\n"
        for ep_id in endpoints:
            ep = endpoints[ep_id]["endpoint"]
            prompt_intro += "* \"%s\": %s\n" % (ep_id, ep.description)
        prompt_intro += "\nFor each endpoint, you must determine if the endpoint should be invoked, " \
                        "based on the description of the endpoint and the message provided by the user.\n" \
                        "You must respond with a JSON object in this format:\n\n" \
                        "[\n" \
                        "   {\n" \
                        "       \"id\": \"ID_OF_ENDPOINT_TO_INVOKE_1\",\n" \
                        "       \"substring\": \"SPECIFIC_SUBSTRING_OF_MESSAGE_REFERRING_TO_THIS_ENDPOINT\"\n" \
                        "   },\n" \
                        "   {\n" \
                        "       \"id\": \"ID_OF_ENDPOINT_TO_INVOKE_2\",\n" \
                        "       \"substring\": \"SPECIFIC_SUBSTRING_OF_MESSAGE_REFERRING_TO_THIS_ENDPOINT\"\n" \
                        "   }\n" \
                        "]\n" \
                        "The list should contain an entry for every API endpoint that should be invoked.\n" \
                        "The \"substring\" field is optional. " \
                        "If there is a specific substring (i.e. a part of the message that is *not* the full string) " \
                        "in the message that provides context for the specific endpoint, include it in the \"substring\" field.\n" \
                        "The substring can span multiple sentences. It does not need to be bound to oke sentence. If you include a substring, please make sure it contains all possible context.\n" \
                        "If there is no specific substring, please omit the \"substring\" field entirely.\n" \
                        "If you decide that no endpoints should be invoked, respond with an empty list: []\n" \
                        "Do not include any other text in your response; only respond with the JSON object."
        prompt_content = "%s" % message

        # attempt to use the LLM to determine which endpoints should be
        # invoked. Parse the response as JSON
        fail_count = 0
        endpoints_to_invoke = []
        for attempt in range(self.config.nla_dialogue_retry_count):
            try:
                r = self.dialogue.oneshot(prompt_intro, prompt_content)

                # attempt to parse and verify the contents of the JSON;
                # retry on failure
                endpoint_id_list = json.loads(r)

                # make sure the response is a list
                if not isinstance(endpoint_id_list, list):
                    raise Exception("LLM's response did not contain a list.")

                # iterate through the list and match each string to an endpoint ID
                for entry in endpoint_id_list:
                    entry_id = str(entry["id"]).lower().strip()

                    # set up an object containing invocation parameters for the
                    # NLA endpoint
                    invoke_params = NLAEndpointInvokeParameters.from_json({
                        "message": message,
                        "extra_params": {
                            "request_data": request_data
                        }
                    })

                    # get the substring field from the parsed JSON, if it was
                    # provided
                    if "substring" in entry:
                        substr = entry["substring"]
                        if substr is not None and len(str(substr).strip()) > 0:
                            invoke_params.substring = str(entry["substring"]).strip()

                    # if the ID string points to one of the endpoints, create a
                    # deep copy of the object and add it to the list of
                    # endpoints to invoke. Additionally, modify the object to
                    # store the invocation parameters; these'll be needed later
                    #
                    # (we do a deep copy, because the same endpoint may end up
                    # getting invoked more than once, each time with different
                    # paramters)
                    if entry_id in endpoints:
                        ep_copy = copy.deepcopy(endpoints[entry_id])
                        ep_copy["invoke_params"] = invoke_params
                        endpoints_to_invoke.append(ep_copy)

                # break out of the loop on the first success
                break
            except Exception as e:
                msg = "Failed to process NLA prompt: %s." % e
                if attempt < self.config.nla_dialogue_retry_count - 1:
                    msg += " Retrying..."
                self.log.write(msg)
                fail_count += 1
                continue

        # if all attempts failed, return early
        if fail_count >= self.config.nla_dialogue_retry_count:
            self.log.write("Failed to assign NLA endpoints to user message after %d attempts." %
                           self.config.nla_dialogue_retry_count)
            return results

        # log the endpoints that were selected
        endpoints_to_invoke_len = len(endpoints_to_invoke)
        if endpoints_to_invoke_len > 0:
            self.log.write("Received user message that was assigned NLA endpoints: \"%s\"" % message)
            ep_id_str = ", ".join([ep["id"] for ep in endpoints_to_invoke])
            self.log.write("NLA Endpoints to invoke based on user message: %s" % ep_id_str)

        # for each endpoint that should be invoked, push them to the NLA queue,
        # to be invoked asynchronously
        queued_entries = []
        for ep_info in endpoints_to_invoke:
            entry = self.nla_queue.push(ep_info)
            queued_entries.append(entry)

        # next, wait for all entries to be completed, and gather their results
        # into a list
        for entry in queued_entries:
            # wait on this entry until its status has been updated to indicate
            # either a failure or a success
            while entry.status not in [SpeakerNLAQueueEntryStatus.SUCCESS,
                                       SpeakerNLAQueueEntryStatus.FAILURE]:
                entry.wait()

            # if the invocation failed, log it and skip
            if entry.status == SpeakerNLAQueueEntryStatus.FAILURE:
                self.log.write("Invocation of NLA endpoint \"%s\" failed." % entry.info["id"])
                continue

            # otherwise, retrieve the result and add it to the list of results
            if entry.result is not None:
                # add the result object to the full endpoint info dict/object
                entry.info["result"] = entry.result
                results.append(entry.info)

        return results

    # Takes a list of dictionary objects (returned by `nla_process()`) and
    # builds a nicely-formatted message that can be sent back to the user.
    def nla_compose_message(self, nla_results: list):
        raw_combined_msg = ""
        raw_combined_msg_ctx = ""
        nla_results_len = len(nla_results)
        for (i, result_info) in enumerate(nla_results):
            # skip if there is no message
            result = result_info["result"]
            if result.message is None:
                continue
            msg = result.message.strip()
            if len(msg) == 0:
                continue

            # otherwise, append the result's message into a combined message
            raw_combined_msg += msg
            if i < nla_results_len - 1:
                raw_combined_msg += "\n\n"

            # if message context was provided, append it to the combined
            # context string. This information will be presented to the LLM
            # when we ask it to reword things
            if result.message_context is not None:
                ctx = result.message_context.strip()
                if len(ctx) > 0:
                    raw_combined_msg_ctx += "%s\n\n" % ctx

        # if the combined message is empty (meaning no NLA endpoints returned a
        # message string), return None
        if len(raw_combined_msg) == 0:
            return None

        # create a prompt to tell the LLM how to reword the message
        reword_context = "This message contains a list of actions performed, or information retrieved, " \
                         "by a home assistant.\n" \
                         "Reword the message such that the sentences and information flow together naturally.\n"
        if len(raw_combined_msg_ctx) > 0:
            reword_context += "The following additional context is provided; " \
                              "please consider this when rewording the message:\n\n%s" % \
                              raw_combined_msg_ctx

        # next, invoke the again to reword the message into something more well
        # formatted and human-like
        if len(raw_combined_msg) > 0:
            try:
                reworded_msg = self.dialogue.reword(raw_combined_msg,
                                                    extra_context=reword_context)
                return reworded_msg
            except Exception as e:
                self.log.write("Failed to reword NLA response message: %s" % e)
                return raw_combined_msg

        return raw_combined_msg


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
                author = DialogueAuthor.from_json({
                    "name": name,
                    "type": DialogueAuthorType.USER.name,
                })

            # before passing anything to the dialogue library, try to parse the
            # text as a call to action. If successful, an array of messages will
            # be returned.
            nla_results = self.service.nla_process(msg, flask.g.jdata)
            nla_results_len = len(nla_results)
            # if at least one result was returned, build a response message
            # containing all the nla_results, send it, and return
            if nla_results_len > 0:
                # process all the results and convert them into a
                # nicely-formatted response message
                nla_response_msg = self.service.nla_compose_message(nla_results)

                if nla_response_msg is None:
                    return self.make_response(success=True)

                # build a payload to respond with, containing the message
                rdata = {
                    "response": nla_response_msg
                }
                return self.make_response(payload=rdata)

            # otherwise, if no NLA endpoints were processed above, send the
            # message to the dialogue interface for a standard chat-bot
            # experience
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
if __name__ == "__main__":
    cli = ServiceCLI(config=SpeakerConfig, service=SpeakerService, oracle=SpeakerOracle)
    cli.run()

