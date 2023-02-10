#!/usr/bin/python3
# This service implements a Telegram bot that I use to communicate with DImROD.
#   https://core.telegram.org/bots/api
#   https://pypi.org/project/pyTelegramBotAPI/

# Imports
import os
import sys
import time
import flask
import telebot
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession
from lib.cli import ServiceCLI

# Service imports
from telegram_objects import TelegramChat, TelegramUser
from command import TelegramCommand
from commands.help import command_help
from commands.system import command_system
from commands.lights import command_lights
from commands.network import command_network
from commands.weather import command_weather
from commands.event import command_event
from commands.list import command_list


# =============================== Config Class =============================== #
class TelegramConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("bot_api_key",              [str],      required=True),
            ConfigField("bot_chats",                [list],     required=True),
            ConfigField("bot_users",                [list],     required=True),
            ConfigField("lumen_address",            [str],      required=True),
            ConfigField("lumen_port",               [int],      required=True),
            ConfigField("lumen_auth_username",      [str],      required=True),
            ConfigField("lumen_auth_password",      [str],      required=True),
            ConfigField("warden_address",           [str],      required=True),
            ConfigField("warden_port",              [int],      required=True),
            ConfigField("warden_auth_username",     [str],      required=True),
            ConfigField("warden_auth_password",     [str],      required=True),
            ConfigField("scribble_address",         [str],      required=True),
            ConfigField("scribble_port",            [int],      required=True),
            ConfigField("scribble_auth_username",   [str],      required=True),
            ConfigField("scribble_auth_password",   [str],      required=True)
        ]


# ============================== Service Class =============================== #
class TelegramService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = TelegramConfig()
        self.config.parse_file(config_path)
        self.refresh()

        # define the bot's commands
        self.commands = [
            TelegramCommand(["help", "commands", "what"],
                            "Presents this help menu.",
                            command_help),
            TelegramCommand(["system", "status", "vitals"],
                            "Reports system information.",
                            command_system),
            TelegramCommand(["lights", "light", "lumen"],
                            "Interacts with the home lights.",
                            command_lights),
            TelegramCommand(["net", "network", "wifi"],
                            "Retrieves home network info.",
                            command_network),
            TelegramCommand(["weather", "forecast", "nimbus"],
                            "Reports the weather.",
                            command_weather),
            TelegramCommand(["event", "task", "taskmaster"],
                            "Carries out event-specific tasks.",
                            command_event),
            TelegramCommand(["list"],
                            "Updates and retrieves lists.",
                            command_list)
        ]

        # parse each chat as a TelegramChat object
        self.chats = []
        for cdata in self.config.bot_chats:
            tc = TelegramChat()
            tc.parse_json(cdata)
            self.chats.append(tc)

        # parse each user as a TelegramUser object
        self.users = []
        for udata in self.config.bot_users:
            tu = TelegramUser()
            tu.parse_json(udata)
            self.users.append(tu)
    
    # ------------------------------- Helpers -------------------------------- #
    # Sets up a new TeleBot instance.
    def refresh(self):
        self.bot = telebot.TeleBot(self.config.bot_api_key)

    # Takes in a message and checks the chat-of-origin (or user-of-origin) and
    # returns True if the user/chat is whitelisted. Returns False otherwise.
    def check_message(self, message):
        # first, check the chat ID
        chat_id = str(message.chat.id)
        chat_is_valid = False
        for chat in self.chats:
            if chat.id == chat_id:
                chat_is_valid = True
                break
        if not chat_is_valid:
            return False

        # next, check the user ID
        user_id = str(message.from_user.id)
        user_is_valid = False
        for user in self.users:
            if user.id == user_id:
                user_is_valid = True
                break
        return user_is_valid

    # ------------------------------ Messaging ------------------------------- #
    # Wrapper for sending a message.
    def send_message(self, chat_id, message, parse_mode=None):
        # try sending the message a finite number of times
        tries = 8
        for i in range(tries):
            try:
                return self.bot.send_message(chat_id, message, parse_mode=parse_mode)
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to send message. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                self.refresh()
                time.sleep(1)
        self.log.write("Failed to send message %d times. Giving up." % tries)

    # ----------------------------- Bot Behavior ----------------------------- #
    # Main runner function.
    def run(self):
        super().run()

        # Generic message handler.
        @self.bot.message_handler()
        def bot_handle_message(message):
            if not self.check_message(message):
                return

            # split the message into pieces and look for a command name (it must
            # begin with a "/" to be a command)
            args = message.text.split()
            first = args[0].strip().lower()
            if first.startswith(TelegramCommand.prefix):
                for command in self.commands:
                    if command.match(first):
                        command.run(self, message, args)
                        return
                # if we didn't find a matching command, tell the user
                self.send_message(message.chat.id,
                                      "Sorry, that's not a valid command.\n"
                                      "Try /help.")
                return

            # if a matching command wasn't found, we'll interpret it as a chat
            # message to dimrod
            # TODO
            self.send_message(message.chat.id,
                                  "Sorry, I'm not sure what you mean.\n"
                                  "Try /help.")

        # start the bot and set it to poll periodically for updates (catch
        # errors and restart when necessary)
        while True:
            try:
                self.log.write("Beginning to poll Telegram API...")
                self.bot.polling()
            except Exception as e:
                self.log.write("Polling failed: %s" % e)
                self.log.write("Waiting for a short time and restarting...")
                time.sleep(5)


# ============================== Service Oracle ============================== #
class TelegramOracle(Oracle):
    def endpoints(self):
        super().endpoints()
        
        # Endpoint used to retrieve a list of whitelisted chats for the bot.
        @self.server.route("/bot/chats", methods=["GET"])
        def endpoint_bot_chats():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # pack the list of chats into a JSON list and return it
            chats = []
            for chat in self.service.chats:
                chats.append(chat.to_json())
            return self.make_response(payload=chats)

        # Endpoint used to retrieve a list of whitelisted users for the bot.
        @self.server.route("/bot/users", methods=["GET"])
        def endpoint_bot_users():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # pack the list of users into a JSON list and return it
            users = []
            for user in self.service.users:
                users.append(user.to_json())
            return self.make_response(payload=users)

        # Endpoint used to instruct the bot to send a message.
        @self.server.route("/bot/send", methods=["POST"])
        def endpoint_bot_send():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # look for a "message" field in the JSON data
            if "message" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message provided.")
            
            # look for a "chat" object in the JSON data
            chat_id = None
            if "chat" in flask.g.jdata:
                try:
                    chat = TelegramChat()
                    chat.parse_json(flask.g.jdata["chat"])
                    chat_id = chat.id
                except Exception as e:
                    return self.make_response(success=False,
                                              msg="Invalid chat data: %s" % e)

            # alternatively, look for a "user" object in the JSON data
            if chat_id is None and "user" in flask.g.jdata:
                try:
                    user = TelegramUser()
                    user.parse_json(flask.g.jdata["user"])
                    chat_id = user.id
                except Exception as e:
                    return self.make_response(success=False,
                                              msg="Invalid user data: %s" % e)

            # make sure we have a chat ID to work with
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # send the message and respond
            self.service.send_message(chat_id, flask.g.jdata["message"])
            return self.make_response(msg="Message sent successfully.")

# =============================== Runner Code ================================ #
cli = ServiceCLI(config=TelegramConfig, service=TelegramService, oracle=TelegramOracle)
cli.run()

