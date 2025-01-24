# A small module that defines class wrappers for Telegram chat and user objects.

# Imports
import os
import sys
import telebot
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Parent class for all Telegram objects.
class TelegramObject(Config):
    @classmethod
    def from_telegram_to_obj(cls, obj):
        result = cls()
        result.parse_json(cls.from_telegram_to_json(obj))
        return result

class TelegramChat(TelegramObject):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],      required=True),
            ConfigField("name",     [str],      required=False, default=None)
        ]

    @staticmethod
    def from_telegram_to_json(obj: telebot.types.Chat):
        jdata = {
            "id": str(obj.id),
        }

        # set a chat name based on whether or not the object has a title
        if obj.title is not None:
            jdata["name"] = obj.title
        return jdata

class TelegramUser(TelegramObject):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],      required=True),
            ConfigField("name",     [str],      required=True)
        ]

    @staticmethod
    def from_telegram_to_json(obj: telebot.types.User):
        jdata = {
            "id": str(obj.id),
            "name": obj.username
        }
        return jdata

class TelegramButton(TelegramObject):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],      required=True),
            ConfigField("text",     [str],      required=True)
        ]

    @staticmethod
    def from_telegram_to_json(obj: telebot.types.InlineKeyboardButton):
        jdata = {
            "id": obj.callback_data,
            "text": obj.text
        }
        return jdata

class TelegramMessage(TelegramObject):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",      [str],       required=True),
            ConfigField("from",    [dict],      required=True),
            ConfigField("chat",    [TelegramChat], required=True),
            ConfigField("text",    [str],       required=True),
            ConfigField("date",    [datetime],  required=True),
            ConfigField("buttons", [TelegramButton], required=True),
        ]

    @staticmethod
    def from_telegram_to_json(obj: telebot.types.Message):
        jdata = {
            "id": str(obj.message_id),
            "from": TelegramUser.from_telegram_to_json(obj.from_user),
            "chat": TelegramChat.from_telegram_to_json(obj.chat),
            "text": obj.text,
            "date": datetime.fromtimestamp(obj.date).isoformat(),
            "buttons": []
        }

        # for each of the message's inline keyboard markup, create a
        # TelegramButton object and add it to the list
        for btn in obj.reply_markup.keyboard:
            jdata["buttons"].append(TelegramButton.from_telegram_to_json(btn[0]))
        return jdata

