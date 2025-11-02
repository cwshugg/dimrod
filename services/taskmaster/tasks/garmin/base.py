# Imports
import os
import sys
from datetime import datetime
import time

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskConfig, TaskJob
import lib.dtu as dtu
from lib.oracle import OracleSession
from lib.garmin.garmin import GarminConfig, Garmin, GarminLoginStatus
from lib.garmin.database import GarminDatabaseConfig, GarminDatabase
from lib.dialogue import DialogueConversation, DialogueMessage, \
                         DialogueAuthorType

# Base class for all Garmin-based taskjobs.
class TaskJob_Garmin(TaskJob):
    def init(self):
        self.refresh_rate = 3600 * 4 # by default, refresh every few hours

        # Garmin 2FA tokens timeout after 30 minutes. We will repeatedly wait
        # for a new message to arrive until the timeout is reached
        self.auth_2fa_timeout = 1800
        self.auth_2fa_poll_time_default = 60 # by default, check every minute
        self.auth_2fa_poll_timings = \
            [5] * 30 + \
            [10] * 15 + \
            [30] * 10

        # look for a local config file containing garmin info, with which we'll
        # set up a Garmin API object
        fdir = os.path.dirname(os.path.realpath(__file__))
        config_fname = "garmin.json"
        config_fpath = os.path.join(fdir, config_fname)
        self.garmin_config = GarminConfig.from_file(config_fpath)

        # set up a path to use for storing Garmin data in a database
        self.db_config_fname = "garmin_database.json"
        self.db_config_fpath = os.path.join(fdir, self.db_config_fname)

    # Update function to be overridden by subclasses.
    def update(self, todoist, gcal):
        pass

    # Loads the database config object and creates (and returns) a
    # `GarminDatabase` object.
    def get_database(self):
        db_config = GarminDatabaseConfig.from_file(self.db_config_fpath)
        return GarminDatabase(db_config)

    # Retrieves an authenticated Garmin client.
    # If retrieving the client fails, this object's refresh rate is updated to
    # try again sooner, and `None` if returned.
    def get_client(self):
        g = self.authenticate()
        if g is not None:
            return g

        self.log("Failed to authenticate with Garmin API. "
                 "Will retry in %d seconds." % self.refresh_rate)
        return None

    # Performs authentication with Garmin (including handling 2FA, if
    # necessary).
    #
    # Returns the authenticated Garmin object, or `None` on failure.
    def authenticate(self):
        # attempt to log in with an existing, local token store. If this
        # succeeded, then the token is still valid and no further action is
        # needed
        g = Garmin(self.garmin_config)
        lwt = g.login_with_tokenstore()
        if lwt == GarminLoginStatus.SUCCESS:
            return g

        # if that failed, attempt to log in with credentials
        self.log("Failed to log into Garmin API with existing auth token. "
                 "Attempting to generate new token...")

        # log into Garmin with the configured credentials
        lwc = g.login_with_credentials()
        if lwc == GarminLoginStatus.NEED_2FA:
            # if 2-factor authentication is needed, send a question via
            # Telegram. This is how we'll ask the user for the 2FA code, so we
            # can log in
            ts = self.get_telegram_session()
            q = "🔑 Please reply to this message with your Garmin 2FA code.\n\n" \
                "This code was requested to generate a new Garmin API auth token."
            convo = self.send_question(self.garmin_config.auth_2fa_telegram_chat, q)
            self.log("Sent 2FA code request. Received conversation info: %s" % convo)

            # wait for the user to reply with the 2FA code. If they don't reply
            # in time, return early
            answer = self.wait_for_question(convo)
            if answer is None:
                self.log("Did not receive 2FA code in time.")
                return None

            auth_2fa_code = answer.strip()
            self.log("Received 2FA code: %s" % auth_2fa_code)

            # attempt to log in with the 2FA code we received
            lw2 = g.login_with_2fa(auth_2fa_code)
            if lw2 != GarminLoginStatus.SUCCESS:
                self.log("Failed to log into Garmin API with 2FA code: %s" % lw2)
                return None

            # at this point, we know logging in with 2FA succeeded; send a
            # message to tell the user authentication succeeded
            msg = "🔓 Successfully generated a new Garmin API auth token. Thanks!"
            self.send_message(self.garmin_config.auth_2fa_telegram_chat, msg)

        return g

    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.service.config.telegram)
        s.login()
        return s

    # Sends a question via Telegram.
    def send_question(self, chat_id: str, text: str):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to telegram to deliver the question to
        # the telegram chat
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        r = telegram_session.post("/bot/send/question", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to send question via Telegram: %s" % \
               telegram_session.get_response_message(r)

        convo = telegram_session.get_response_json(r)
        return DialogueConversation.from_json(convo)

    def wait_for_question(self, convo):
        answer = None
        convo_start = convo.time_start

        # open an authenticated session with the Speaker service
        ss = self.service.get_speaker_session()

        # loop until we get an answer
        poll_idx = 0
        poll_array_len = len(self.auth_2fa_poll_timings)
        while answer is None:
            # have we exceeded the timeout? if so, return None
            now = datetime.now()
            if dtu.diff_in_seconds(now, convo_start) > self.auth_2fa_timeout:
                self.log("Timed out waiting for answer to 2FA question.")
                return None

            # put together a payload to check for the latest update from the
            # conversation
            payload = {"conversation_id": convo.id}
            r = ss.post("/conversation/get_last_update", payload=payload)

            # make sure retrieving the conversation succeeded
            assert ss.get_response_success(r), \
                   "Failed to get last update for conversation %s: %s" % \
                   (convo.id, ss.get_response_message(r))

            # get the message object from the response
            msg = DialogueMessage.from_json(ss.get_response_json(r))

            # is this message of the correct type? (a response to a system
            # question?) If so, we have our answer
            if msg.author.type == DialogueAuthorType.USER_ANSWER_TO_QUERY:
                answer = msg.content
                self.log("Received answer to 2FA question: %s" % answer)
                return answer

            # otherwise, sleep for a time and try again. Cycle through the poll
            # times, so we check less frequently as time goes on
            poll_time = self.auth_2fa_poll_time_default
            if poll_idx < poll_array_len:
                poll_time = self.auth_2fa_poll_timings[poll_idx]
                poll_idx += 1
            time.sleep(poll_time)

    # Sends a message to Telegram.
    def send_message(self, chat_id: str, text: str):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        r = telegram_session.post("/bot/send/message", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to send message via Telegram: %s" % \
               telegram_session.get_response_message(r)

        message = telegram_session.get_response_json(r)
        return message

