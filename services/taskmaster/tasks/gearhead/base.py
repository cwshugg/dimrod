# This module defines the base class for all Gearhead-related TaskJobs within
# the Taskmaster service. It provides helper methods for communicating with both
# the Gearhead service and the Telegram bot via OracleSession.

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
from task import TaskJob
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession, OracleSessionConfig
from lib.dialogue import DialogueConversation, DialogueMessage, \
                         DialogueAuthorType


class TaskJob_Gearhead_Config(Config):
    """Configuration for Gearhead-related TaskJobs.

    Contains connection details for the Gearhead service oracle, the Telegram
    chat ID used for user interaction, and an optional check-in interval (in
    days) that controls how frequently the mileage check-in runs.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            ConfigField("gearhead",                 [OracleSessionConfig],  required=True),
            ConfigField("telegram_chat_id",         [str],                  required=True),
            ConfigField("checkin_interval_days",     [int],                  required=False, default=14),
        ]


class TaskJob_Gearhead(TaskJob):
    """Base class for all Gearhead-related TaskJobs.

    Provides shared helpers for obtaining authenticated OracleSession handles
    to both the Gearhead service and the Telegram bot. Also provides
    convenience methods for sending Telegram questions, waiting for replies,
    and sending plain messages — mirroring the pattern established in the
    Garmin TaskJob base class.
    """
    def init(self):
        """Initialization hook called before any calls to ``update()``.

        Loads the Gearhead TaskJob config from the JSON file co-located with
        this module and sets a default refresh rate.
        """
        # default refresh rate: every 14 days (in seconds)
        self.refresh_rate = 86400 * 14

        # timeouts and polling configuration for waiting on Telegram replies
        self.reply_timeout = 3600 * 8       # 8 hour timeout for a reply
        self.reply_poll_time_default = 60   # default poll interval (seconds)
        self.reply_poll_timings = \
            [5] * 30 + \
            [10] * 15 + \
            [30] * 10

        # load the local config file
        config_fname = "mileage_checkin.json"
        config_fpath = os.path.join(fdir, config_fname)
        self.gearhead_config = TaskJob_Gearhead_Config()
        self.gearhead_config.parse_file(config_fpath)

    def update(self):
        """Update function to be overridden by subclasses."""
        pass

    # ------------------------------- Sessions ------------------------------- #
    def get_gearhead_session(self):
        """Creates and returns an authenticated OracleSession with the Gearhead
        service oracle.
        """
        s = OracleSession(self.gearhead_config.gearhead)
        s.login()
        return s

    def get_telegram_session(self):
        """Creates and returns an authenticated OracleSession with the Telegram
        bot oracle.
        """
        s = OracleSession(self.service.config.telegram)
        s.login()
        return s

    # ----------------------------- Telegram I/O ----------------------------- #
    def send_question(self, chat_id: str, text: str):
        """Sends a question via Telegram and returns a ``DialogueConversation``
        representing the newly-created conversation.

        The Telegram bot will display the question and expect a user reply.
        """
        telegram_session = self.get_telegram_session()

        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        r = telegram_session.post("/bot/send/question", payload=payload)

        assert telegram_session.get_response_success(r), \
               "Failed to send question via Telegram: %s" % \
               telegram_session.get_response_message(r)

        convo = telegram_session.get_response_json(r)
        return DialogueConversation.from_json(convo)

    def wait_for_reply(self, convo):
        """Polls the Speaker service for the user's reply to a previously-sent
        question.

        Returns the reply text as a string, or ``None`` if the timeout is
        reached without a reply.
        """
        answer = None
        convo_start = convo.time_start

        # open an authenticated session with the Speaker service
        ss = self.service.get_speaker_session()

        poll_idx = 0
        poll_array_len = len(self.reply_poll_timings)
        while answer is None:
            # check if we've exceeded the timeout
            now = datetime.now()
            if dtu.diff_in_seconds(now, convo_start) > self.reply_timeout:
                self.log("Timed out waiting for Telegram reply.")
                return None

            # query the Speaker for the latest conversation update
            payload = {"conversation_id": convo.id}
            r = ss.post("/conversation/get_last_update", payload=payload)

            assert ss.get_response_success(r), \
                   "Failed to get last update for conversation %s: %s" % \
                   (convo.id, ss.get_response_message(r))

            # parse the message from the response
            msg = DialogueMessage.from_json(ss.get_response_json(r))

            # if the message is a user answer to our query, we have our reply
            if msg.author.type == DialogueAuthorType.USER_ANSWER_TO_QUERY:
                answer = msg.content
                self.log("Received reply: %s" % answer)
                return answer

            # otherwise, sleep and try again with progressive back-off
            poll_time = self.reply_poll_time_default
            if poll_idx < poll_array_len:
                poll_time = self.reply_poll_timings[poll_idx]
                poll_idx += 1
            time.sleep(poll_time)

    def send_message(self, chat_id: str, text: str):
        """Sends a plain (non-question) message via Telegram.

        Returns the raw message payload from the Telegram bot.
        """
        telegram_session = self.get_telegram_session()

        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        r = telegram_session.post("/bot/send/message", payload=payload)

        assert telegram_session.get_response_success(r), \
               "Failed to send message via Telegram: %s" % \
               telegram_session.get_response_message(r)

        message = telegram_session.get_response_json(r)
        return message
