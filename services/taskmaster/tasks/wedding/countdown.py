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
from task import TaskConfig
from tasks.wedding.base import *
import lib.dtu as dtu
from lib.config import Config, ConfigField

class TaskJob_Wedding_Countdown_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("telegram_chat_ids",    [list],     required=True),
            ConfigField("wedding_date",         [datetime], required=True),
        ]


class TaskJob_Wedding_Countdown(TaskJob_Wedding):
    def init(self):
        self.refresh_rate = 3600

        # find the config and parse it
        config_dir = os.path.dirname(os.path.realpath(__file__))
        config_name = os.path.basename(__file__.replace(".py", ".json"))
        config_path = os.path.join(config_dir, config_name)
        self.config = TaskJob_Wedding_Countdown_Config()
        self.config.parse_file(config_path)

    def update(self, todoist, gcal):
        now = datetime.now()

        # if the wedding date has passed, do nothing
        if now.timestamp() > self.config.wedding_date.timestamp():
            return False
        
        # only trigger at a certain hour
        if now.hour != 19:
            return False
        
        # determine the number of days until the wedding, and send a message
        days = round(dtu.diff_in_days(self.config.wedding_date, now))
        ts = self.get_telegram_session()
        for chat_id in self.config.telegram_chat_ids:
            self.send_message(chat_id, "There are %d days until the wedding!" % days)
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

