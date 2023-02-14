#!/usr/bin/python3
# My reminder service, modeled after cron jobs.

# Imports
import os
import sys
import json
import flask
import time

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession
from lib.cli import ServiceCLI
from lib.mail import MessengerConfig, Messenger

# Service imports
from reminder import Reminder


# =============================== Config Class =============================== #
class NotifConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("reminder_dir",             [str],  required=True),
            ConfigField("telegram_addr",            [str],  required=True),
            ConfigField("telegram_port",            [int],  required=True),
            ConfigField("telegram_auth_username",   [str],  required=True),
            ConfigField("telegram_auth_password",   [str],  required=True),
            ConfigField("messenger_webhook_event",  [str],  required=True),
            ConfigField("webhook_key",              [str],  required=True),
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
                    
                    # check all reminders for readiness
                    count = check_all(rems)
                    if count > 0:
                        self.log.write("Sent %d reminders from %s." % (count, f))
                    
                    # while we're at it, look at all the reminders that were
                    # loaded in. If *all* of them exist in the past, we can
                    # delete this file to prevent buildup
                    # TODO TODO TODO

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
            
            # compose a message and send it to the telegram service for delivery
            msg = "<b>%s</b>\n\n%s" % (rem.title, rem.message)
            msg_data = {"chat": matched_chat, "message": msg}
            r = telegram_session.post("/bot/send", payload=msg_data)
            self.log.write(" - Telegrammed \"%s\"." % matched_chat["name"])

            # check telegram's response and write a log message
            jdata = r.json()
            log_msg = "    - Telegram responded with code %d." % r.status_code
            if "message" in jdata and len(jdata["message"]) > 0:
                log_msg += " \"%s\"" % jdata["message"]
            self.log.write(log_msg)

    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.config.telegram_addr, self.config.telegram_port)
        s.login(self.config.telegram_auth_username,
                self.config.telegram_auth_password)
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
            
            # TODO create the reminder
        

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=NotifConfig, service=NotifService, oracle=NotifOracle)
cli.run()

