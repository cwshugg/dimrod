#!/usr/bin/python3
# This service implements a Telegram bot that I use to communicate with DImROD.
#   https://core.telegram.org/bots/api
#   https://pypi.org/project/pyTelegramBotAPI/

# Imports
import os
import sys
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


# =============================== Config Class =============================== #
class TelegramConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("bot_api_key",          [str],      required=True),
            ConfigField("bot_chats",            [list],     required=True),
            ConfigField("bot_users",            [list],     required=True),
            ConfigField("lumen_address",        [str],      required=True),
            ConfigField("lumen_port",           [int],      required=True),
            ConfigField("lumen_auth_username",  [str],      required=True),
            ConfigField("lumen_auth_password",  [str],      required=True)
        ]


# ============================== Service Class =============================== #
class TelegramService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = TelegramConfig()
        self.config.parse_file(config_path)
        self.bot = telebot.TeleBot(self.config.bot_api_key)

        # define the bot's commands
        self.commands = [
            TelegramCommand(["help", "commands", "what"],
                            "Presents this help menu.",
                            self.command_help),
            TelegramCommand(["check", "status", "vitals"],
                            "Reports status information.",
                            self.command_status),
            TelegramCommand(["lights", "light", "lumen"],
                            "Interacts with the home lights.",
                            self.command_lights),
            TelegramCommand(["net", "network", "wifi"],
                            "Retrieves home network info.",
                            self.command_network),
            TelegramCommand(["weather", "forecast", "nimbus"],
                            "Reports the weather.",
                            self.command_weather),
            TelegramCommand(["event", "task", "taskmaster"],
                            "Carries out event-specific tasks.",
                            self.command_event),
            TelegramCommand(["list"],
                            "Updates and retrieves lists.",
                            self.command_list)
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

    # ------------------------------- Commands ------------------------------- #
    # The help command's handler function.
    def command_help(self, message: dict, args: list):
        # build a table of possible commands in HTML
        # https://core.telegram.org/bots/api#markdownv2-style
        msg = "<b>All possible commands</b>\n\n"
        for command in self.commands:
            msg += "<code>%s</code> - %s\n" % \
                   (command.keywords[0], command.description)
        self.bot.send_message(message.chat.id, msg, parse_mode="HTML")
    
    # The status command's handler.
    def command_status(self, message: dict, args: list):
        msg = "DImROD is up and running."
        self.bot.send_message(message.chat.id, msg)

    # The light command's handler.
    def command_lights(self, message: dict, args: list):
        # create a HTTP session with lumen
        session = OracleSession(self.config.lumen_address,
                                self.config.lumen_port)
        try:
            r = session.login(self.config.lumen_auth_username,
                              self.config.lumen_auth_password)
        except Exception as e:
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't reach Lumen. "
                                  "It might be offline.")
            return False

        # check the login response
        if r.status_code != 200:
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't authenticate with Lumen.")
            return False
        if not session.get_response_success(r):
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't authenticate with Lumen. "
                                  "(%s)" % session.get_response_message(r))
            return False

        # if no other arguments were specified, we'll generate a list of names
        # for the lights around the house
        if len(args) == 1:
            r = session.get("/lights")
            try:
                lights = session.get_response_json(r)
                msg = "<b>All connected lights</b>\n\n"
                for light in lights:
                    msg += "• <code>%s</code> - %s\n" % \
                            (light["id"], light["description"])
                self.bot.send_message(message.chat.id, msg, parse_mode="HTML")
                return True
            except Exception as e:
                self.bot.send_message(message.chat.id,
                                      "Sorry, I couldn't retrieve light data. "
                                      "(%s)" % e)
                return False

        msg = "I'm not sure what you meant."
        self.bot.send_message(message.chat.id, msg)

    # The network command's handler.
    def command_network(self, message: dict, args: list):
        # create a HTTP session with warden
        session = OracleSession(self.config.warden_address,
                                self.config.warden_port)
        try:
            r = session.login(self.config.warden_auth_username,
                              self.config.warden_auth_password)
        except Exception as e:
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't reach Warden. "
                                  "It might be offline.")
            return False

        # check the login response
        if r.status_code != 200:
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't authenticate with Warden.")
            return False
        if not session.get_response_success(r):
            self.bot.send_message(message.chat.id,
                                  "Sorry, I couldn't authenticate with Warden. "
                                  "(%s)" % session.get_response_message(r))
            return False

        # if no arguments are specified, we'll list the connected devices
        if len(args) == 1:
            msg = "<b>All cached devices</b>\n\n"
            r = session.get("/clients")
            try:
                clients = session.get_response_json(r)
                for client in clients:
                    last_seen = datetime.fromtimestamp(client["last_seen"])
                    msg += "• <code>%s</code>\n" % client["macaddr"]
                    if "name" in client:
                        msg += "    • <b>Name:</b> %s\n" % client["name"]
                    msg += "    • <b>IP Address:</b> <code>%s</code>\n" % client["ipaddr"]
                    msg += "    • <b>Last seen:</b> %s\n" % \
                           last_seen.strftime("%Y-%m-%d at %H:%M:%S %p")
                self.bot.send_message(message.chat.id, msg, parse_mode="HTML")
            except Exception as e:
                self.bot.send_message(message.chat.id,
                                      "Sorry, I couldn't retrieve network data. "
                                      "(%s)" % e)

        msg = "I'm not sure what you meant."
        self.bot.send_message(message.chat.id, msg)

    # The weather command's handler.
    def command_weather(self, message: dict, args: list):
        msg = "TODO - weather"
        self.bot.send_message(message.chat.id, msg)

    # The event command's handler.
    def command_event(self, message: dict, args: list):
        msg = "TODO - event"
        self.bot.send_message(message.chat.id, msg)

    # The list command's handler.
    def command_list(self, message: dict, args: list):
        msg = "TODO - list"
        self.bot.send_message(message.chat.id, msg)

    # ------------------------------ Interface ------------------------------- #
    # Sends a message to the given Telegram chat ID.
    def send_message(self, chat_id: str, message: str):
        self.bot.send_message(chat_id, message)
    
    # ----------------------------- Bot Behavior ----------------------------- #
    # Main runner function.
    def run(self):
        super().run()

        # Generic message handler.
        @self.bot.message_handler()
        def bot_handle_message(message):
            if not self.check_message(message):
                return
            reply = "Unknown command."

            # split the message into pieces and look for a command name
            args = message.text.split()
            cmd = args[0].strip().lower()
            matched = False
            for command in self.commands:
                if cmd in command.keywords:
                    reply = command.run(message, args)
                    matched = True
                    break
            
            # if no command was found, pass the message to the chat library
            if not matched:
                # TODO
                pass

        # start the bot and set it to poll periodically for updates
        self.bot.polling()


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
