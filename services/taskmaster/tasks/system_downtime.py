# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(fdir))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import *
import lib.dtu as dtu
from lib.config import Config, ConfigField
from lib.oracle import OracleSession

class TaskJob_System_Downtime_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("telegram_chat_ids",    [list],     required=True),
            ConfigField("downtime_threshold",   [int],      required=False, default=600),
        ]


class TaskJob_System_Downtime(TaskJob):
    def init(self):
        self.refresh_rate = 180

        # find the config and parse it
        config_dir = os.path.dirname(os.path.realpath(__file__))
        config_name = os.path.basename(__file__.replace(".py", ".json"))
        config_path = os.path.join(config_dir, config_name)
        self.config = TaskJob_System_Downtime_Config()
        self.config.parse_file(config_path)

    def update(self, todoist, gcal):
        now = datetime.now()
        last_success = self.get_last_success_datetime()

        # This TaskJob aims to detect when the system goes down. It does this
        # by returning `True` (success) constantly, indicating that the code
        # was able to be run (meaning we have power). This updates the
        # taskjob's "last-succeeded" time, which we'll refer to each time to
        # determine how long it's been since we last succeeded.
        #
        # If it's been long enough between the last success and now, we'll
        # consider it to be a downtime.

        # if there isn't a previous timestamp for this taskjob, then it may
        # have never run before. Return early in order to indicate success and
        # have a timestamp saved
        if last_success is None:
            return True
        
        # the the last success within the threshold? If so, we don't consider
        # this to be a downtime
        secs = dtu.diff_in_seconds(now, last_success)
        if secs < self.config.downtime_threshold:
            return True

        # otherwise, the last time this taskjob ran was longer than the
        # threshold we've set. There must have been some sort of downtime.
        self.log("Downtime threshold of %d seconds exceeded." % self.config.downtime_threshold)
        
        # compute the difference in hours/mins/secs, and build a string
        [diff_secs, diff_mins, diff_hours] = dtu.diff_in_seconds_minutes_hours(now, last_success)
        date_str = ""
        if diff_hours > 0:
            date_str += "%dh " % diff_hours
        if diff_mins > 0:
            date_str += "%dm " % diff_mins
        date_str += "%ds" % diff_secs
        
        # construct a message to send
        msg = "⚠️ There seems to have been a system downtime for %s.\n\n" \
              "I am back online now." % \
              date_str
        
        # send the message to all configured telegram chats
        for chat_id in self.config.telegram_chat_ids:
            self.send_message(chat_id, msg)
        return True

    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.service.config.telegram)
        s.login()
        return s
    
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

