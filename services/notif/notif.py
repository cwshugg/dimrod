#!/usr/bin/python3
# My reminder service, modeled after cron jobs.

# Imports
import os
import sys
import json
import flask
import time
import re
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.nla import NLAEndpoint, NLAEndpointHandlerFunction, NLAResult, NLAEndpointInvokeParameters
from lib.dialogue import DialogueConfig, DialogueInterface
from lib.cli import ServiceCLI
from lib.mail import MessengerConfig, Messenger
from lib.ntfy import ntfy_send
import lib.dtu as dtu

# Service imports
from reminder import Reminder


# =============================== Config Class =============================== #
class NotifConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("reminder_dir",             [str],  required=True),
            ConfigField("messenger_webhook_event",  [str],  required=True),
            ConfigField("webhook_key",              [str],  required=True),
            ConfigField("dialogue",                 [DialogueConfig], required=True),
            ConfigField("telegram",     [OracleSessionConfig],  required=True),
            ConfigField("nla_create_reminder_dialogue_retries", [int], required=False, default=4),
        ]


# ============================== Service Class =============================== #
class NotifService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = NotifConfig()
        self.config.parse_file(config_path)

        # initialize the email messenger object
        mconf = MessengerConfig()
        mconf.parse_file(config_path)
        self.emailer = Messenger(mconf)

    # Overridden main function implementation.
    def run(self):
        super().run()

        # if the reminder directory doesn't exist, make it
        if not os.path.isdir(self.config.reminder_dir):
            self.log.write("Reminder directory (%s) doesn't exist. Creating..." %
                           self.config.reminder_dir)
            os.mkdir(self.config.reminder_dir)

        # Helper function that runs through a list of reminders and checks each
        # one for triggering. Returns the number of reminders that were fired.
        def check_all(rems: list):
            count = 0
            for rem in rems:
                if not rem.ready():
                    continue
                self.log.write("Ready reminder: %s" % rem)
                self.send_reminder(rem)
                count += 1
            return count

        # loop indefinitely, checking for reminders every minute
        while True:
            prune_list = []

            # iterate through all files in the reminder directory
            for (root, dirs, files) in os.walk(self.config.reminder_dir):
                for f in files:
                    # skip non-JSON files
                    if not f.endswith(".json"):
                        continue

                    # load the JSON file and parse its reminders
                    fpath = os.path.join(root, f)
                    rems = []
                    try:
                        rems = self.load_reminders(fpath)
                    except Exception as e:
                        self.log.write("Failed to load reminder JSON file %s: %s" %
                                        (f, e))
                        continue

                    # check all reminders for readiness
                    check_all(rems)

                    # while we're at it, look at all the reminders that were
                    # loaded in. If *all* of them exist in the past, we can
                    # delete this file to prevent buildup
                    expired = 0
                    for rem in rems:
                        expired += 1 if rem.expired() else 0
                    if expired == len(rems):
                        prune_list.append(fpath)

            # any files that were deemed to contain only expired reminders will
            # be deleted
            for fpath in prune_list:
                try:
                    self.log.write("Deleting expired reminder file %s." %
                                   os.path.basename(fpath))
                    os.remove(fpath)
                except Exception as e:
                    self.log.write("Failed to delete expired reminder file %s: %s" %
                                   (os.path.basename(fpath), e))

            time.sleep(60)

    # ------------------------------- File IO -------------------------------- #
    # Loads reminders in from a JSON file and returns a list of Reminder
    # objects.
    def load_reminders(self, fpath: str):
        rems = []
        with open(fpath, "r") as fp:
            jdata = json.load(fp)
            for entry in jdata:
                r = Reminder()
                r.parse_json(entry)
                rems.append(r)
        return rems

    # Saves the given reminder to its own file in the reminder directory.
    def save_reminder(self, rem: Reminder):
        fname = ".%s.json" % rem.get_id()
        fpath = os.path.join(self.config.reminder_dir, fname)
        with open(fpath, "w") as fp:
            fp.write(json.dumps([rem.to_json()], indent=4))

    # --------------------------- Reminder Sending --------------------------- #
    # Sends a reminder over one or more mediums, depending on how the reminder
    # was configured.
    def send_reminder(self, rem: Reminder):
        # send to all listed emails
        for email in rem.send_emails:
            subject = "DImROD - %s" % rem.title
            try:
                self.emailer.send(email, subject, rem.message)
                self.log.write(" - Emailed \"%s\"." % email)
            except Exception as e:
                self.log.write("Failed to email \"%s\" - %s" % (email, e))
                continue

        # send to all telegram chats
        telegram_session = None
        telegram_chats = []
        for chat in rem.send_telegrams:
            # set up the telegram session during the first iteration
            if telegram_session is None:
                try:
                    telegram_session = self.get_telegram_session()

                    # retrieve all telegram chats
                    r = telegram_session.get("/bot/chats")
                    telegram_chats = telegram_session.get_response_json(r)
                except Exception as e:
                    self.log.write("Failed to talk to telegram - %s" % e)
                    continue

            # find the correct chat to which we must send data (search by name
            # OR by ID)
            matched_chat = None
            for cdata in telegram_chats:
                if chat.lower() == cdata["id"].lower() or \
                   chat.lower() in cdata["name"].lower():
                    matched_chat = cdata
                    break
            if matched_chat is None:
                self.log.write("Couldn't find a telegram chat that matched \"%s\"." %
                               chat)
                continue

            # compose a message (include the title only if it's not empty)
            msg = rem.message
            title_str = ""
            if len(rem.title) > 0:
                title_has_letters = re.search("[a-zA-Z]", rem.title) is not None
                title_str = "<b>%s%s</b>" % (rem.title, ":" if title_has_letters else "")
                msg = "%s %s" % (title_str, rem.message)

            # pack the message into a payload and send it to the telegram
            # service for delivery
            msg_data = {"chat": matched_chat, "text": msg}
            r = telegram_session.post("/bot/send/message", payload=msg_data)
            self.log.write(" - Telegrammed \"%s\"." % matched_chat["name"])

            # check telegram's response and write a log message
            jdata = r.json()
            log_msg = "    - Telegram responded with code %d." % r.status_code
            if "message" in jdata and len(jdata["message"]) > 0:
                log_msg += " \"%s\"" % jdata["message"]
            self.log.write(log_msg)

        # send to all specified ntfy channels
        for channel in rem.send_ntfys:
            # depending on the content of the title, choose an appropriate
            # title string
            title_is_empty = rem.title is None or len(rem.title) == 0
            title_has_letters = re.search("[a-zA-Z]", rem.title) is not None
            title_str = str(rem.title)
            if title_is_empty:
                title_str = "DImROD Notification"
            elif not title_has_letters and len(title_str) < 10:
                title_str = "DImROD Notification - %s" % title_str

            # send the ntfy HTTP request to post to the channel
            self.log.write(" - Posting a ntfy message to channel \"%s\"" % str(channel))
            r = ntfy_send(str(channel), rem.message, title=title_str)
            self.log.write("    - Ntfy responded with code %d." % r.status_code)

    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.config.telegram)
        s.login()
        return s


# ============================== Service Oracle ============================== #
class NotifOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # Retrieves and returns all lists.
        @self.server.route("/reminder/create", methods=["POST"])
        def endpoint_list_get_all():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(msg="Missing JSON data.",
                                          success=False, rstatus=400)

            # attempt to create a reminder object from the JSON payload
            rem = Reminder()
            try:
                rem.parse_json(flask.g.jdata)
            except Exception as e:
                return self.make_response(msg="Invalid JSON data: %s" % e,
                                          success=False, rstatus=400)

            # save the reminder
            try:
                self.service.save_reminder(rem)
            except Exception as e:
                return self.make_response(msg="Failed to save reminder: %s" % e,
                                          success=False, rstatus=400)

            return self.make_response(msg="Reminder created successfully.",
                                      payload=rem.to_json())


    def init_nla(self):
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                    "name": "create_reminder",
                    "description": "Create a reminder, given a message and a time."
                }).set_handler(nla_create_reminder),
        ]

def nla_create_reminder(oracle: NotifOracle, jdata: dict):
    params = NLAEndpointInvokeParameters.from_json(jdata)

    # create a date string to give to the LLM, for context
    now = datetime.now()
    datetime_str = "%s, %s" % (dtu.format_yyyymmdd_hhmmss_24h(now),
                               dtu.get_weekday_str(now))

    # set up an intro prompt for the LLM
    prompt_intro = "You are a home assistant specializing in creating reminders for the user.\n" \
                   "The current datetime is: %s.\n\n" \
                   "You will receive a sentence from the user that describes two things:\n\n" \
                   "1. The content/message of the reminder.\n" \
                   "2. The time at which the reminder should be sent.\n\n" \
                   "Your task is to extract these pieces of information and respond in the following JSON format:\n\n" \
                   "[\n" \
                   "    {\n" \
                   "        \"title\": \"(OPTIONAL) TITLE OF THE REMINDER\",\n" \
                   "        \"message\": \"CONTENT OF THE REMINDER\",\n" \
                   "        \"trigger_years\": [YEAR1_TO_TRIGGER_ON, YEAR2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_months\": [MONTH1_TO_TRIGGER_ON, MONTH2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_days\": [DAY1_TO_TRIGGER_ON, DAY2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_hours\": [HOUR1_TO_TRIGGER_ON_IN_24H_FORMAT, HOUR2_TO_TRIGGER_ON_IN_24H_FORMAT, ...],\n" \
                   "        \"trigger_minutes\": [MINUTE1_TO_TRIGGER_ON, MINUTE2_TO_TRIGGER_ON, ...],\n" \
                   "    },\n" \
                   "    {\n" \
                   "        \"title\": \"(OPTIONAL) TITLE OF THE REMINDER\",\n" \
                   "        \"message\": \"CONTENT OF THE REMINDER\",\n" \
                   "        \"trigger_years\": [YEAR1_TO_TRIGGER_ON, YEAR2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_months\": [MONTH1_TO_TRIGGER_ON, MONTH2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_days\": [DAY1_TO_TRIGGER_ON, DAY2_TO_TRIGGER_ON, ...],\n" \
                   "        \"trigger_hours\": [HOUR1_TO_TRIGGER_ON_IN_24H_FORMAT, HOUR2_TO_TRIGGER_ON_IN_24H_FORMAT, ...],\n" \
                   "        \"trigger_minutes\": [MINUTE1_TO_TRIGGER_ON, MINUTE2_TO_TRIGGER_ON, ...],\n" \
                   "    }\n" \
                   "]\n" \
                   "\n" \
                   "Each JSON object in the list represents a single reminder to be created.\n" \
                   "The \"message\" field is required and should contain the content of the reminder.\n" \
                   "If the user's message refers to \"me\" or \"my\", please rephrase the reminder's message as though you are speaking *to* the user (\"you\", \"your\", etc.).\n" \
                   "The \"title\" field is optional; if you cannot find a fitting title, omit this field.\n" \
                   "All \"trigger_*\" fields should be lists of integers.\n" \
                   "Collectively, these \"trigger_*\" fields should define the exact datetime(s) at which the reminder should be sent.\n" \
                   "Use the current datetime, and the user's wording, to determine a value for each of these.\n" \
                   "A few other notes to consider when determining the trigger values:\n\n" \
                   "* If no day is explicitly said by the user, but a time is provided, assume that the user wants the next occurrence of that specific time.\n" \
                   "    * Ex: If the user says \"4:45pm\", but says no day, determine when the next occurrence of 4:45pm would be, and set the triggers to reflect this.\n" \
                   "* If a day is specified, but no specific time, assume that the user wants the time to be the same as the *current* time.\n" \
                   "    * Ex: If the user says \"two days from now\", but says no time, use the current time of day when setting the trigger fields.\n" \
                   "\n" \
                   "If you detect that the user wants multiple reminders to be created, please create multiple JSON objects in the list.\n" \
                   "If not enough information is available to determine the contents or the time any reminders, please respond with an empty list: []\n" \
                   "Only respond with the JSON object, and nothing else." \
                   % datetime_str


    # build the user-message prompt
    prompt_content = params.message
    if hasattr(params, "substring"):
        prompt_content = params.substring

    # set up a dialogue interface
    dialogue = DialogueInterface(oracle.service.config.dialogue)

    # parse the response as JSON
    reminders = []
    fail_count = 0
    for attempt in range(oracle.service.config.nla_create_reminder_dialogue_retries):
        try:
            r = dialogue.oneshot(prompt_intro, prompt_content)
            remdata = json.loads(r)

            # if the response is empty, skip
            if len(remdata) == 0:
                continue

            # otherwise, iterate through the reminders and attempt to parse
            for entry in remdata:
                rem = Reminder.from_json(entry)
                rem.check_triggers()

                # append the reminder to the list
                reminders.append(rem)

            # break on the first success
            break
        except Exception as e:
            oracle.service.log.write("Failed to parse LLM response: %s" % e)
            fail_count += 1
            continue

    # if we couldn't parse the response, return an error message
    if fail_count == oracle.service.config.nla_create_reminder_dialogue_retries:
        return NLAResult.from_json({
            "success": False,
            "message": "Something went wrong while trying to set up a reminder."
        })

    # otherwise, if no reminder could be discerned, return an error
    if len(reminders) == 0:
        return NLAResult.from_json({
            "success": True,
            "message": "I didn't have enough information to set up a reminder."
        })

    title_override = None

    # look for telegram chat IDs from the invocation params. We'll use this to
    # know where to send the reminders
    rem_send_telegrams = []
    exparams = params.extra_params
    if exparams is not None:
        # drill down in the JSON object and look for the telegram chat ID
        if "request_data" in exparams:
            reqdata = exparams["request_data"]
            if "telegram_message" in reqdata:
                telegram_info = reqdata["telegram_message"]
                if "chat_id" in telegram_info:
                    rem_send_telegrams.append(str(telegram_info["chat_id"]))
                    # since this came from a telegram message, override the
                    # title with the bell emoji (to mimick the same behavior as
                    # the telegram service when setting reminders via command)
                    title_override = "🔔"

    # submit each reminder object, and compose a message to return
    rem_msgs = []
    for rem in reminders:
        rem.send_telegrams = rem_send_telegrams

        # override the title, if necessary
        if title_override is not None:
            rem.title = title_override

        oracle.service.save_reminder(rem)

        # compose a message for this reminder
        rem_msg = "• I created reminder: \"%s\", triggering on: %s" % \
                  (rem.message,
                  rem.get_trigger_str())
        rem_msgs.append(rem_msg)

    # log the success
    oracle.service.log.write("Created %d reminder(s) via NLA: %s" %
                             (len(reminders),
                             ", ".join([str(rem) for rem in reminders])))

    # compose a final response message
    msg = "I created %d reminder(s):\n\n%s" % (len(reminders), "\n".join(rem_msgs))

    # give some additional context about interpreting/rewording the message
    msg_ctx = "Please reword this such that the trigger datetime information is human-readable.\n"

    return NLAResult.from_json({
        "success": True,
        "message": msg,
        "message_context": msg_ctx,
    })



# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=NotifConfig, service=NotifService, oracle=NotifOracle)
    cli.run()

