#!/usr/bin/python3
# This service implements a Telegram bot that I use to communicate with DImROD.
#   https://core.telegram.org/bots/api
#   https://pypi.org/project/pyTelegramBotAPI/

# Imports
import os
import sys
import time
import re
from datetime import datetime
import flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import traceback
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.cli import ServiceCLI
from lib.google.google_calendar import GoogleCalendarConfig

# Service imports
from telegram_objects import TelegramChat, TelegramUser
from menu import Menu, MenuDatabase
from command import TelegramCommand
from commands.help import command_help
from commands.system import command_system
from commands.lights import command_lights
from commands.network import command_network
from commands.weather import command_weather
from commands.event import command_event
from commands.remind import command_remind
from commands.mode import command_mode
from commands.calendar import command_calendar
from commands.s_reset import command_s_reset
from commands.s_menu import command_s_menu


# =============================== Config Class =============================== #
class TelegramConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("bot_api_key",              [str],      required=True),
            ConfigField("bot_chats",                [list],     required=True),
            ConfigField("bot_users",                [list],     required=True),
            ConfigField("bot_error_retry_attempts", [int],      required=False, default=8),
            ConfigField("bot_error_retry_delay",    [int],      required=False, default=1),
            ConfigField("bot_conversation_timeout", [int],      required=False, default=900),
            ConfigField("bot_menu_db",              [str],      required=False, default=None),
            ConfigField("bot_menu_db_refresh_rate", [int],      required=False, default=5),
            ConfigField("lumen",    [OracleSessionConfig],      required=True),
            ConfigField("warden",   [OracleSessionConfig],      required=True),
            ConfigField("notif",    [OracleSessionConfig],      required=True),
            ConfigField("moder",    [OracleSessionConfig],      required=True),
            ConfigField("speaker",  [OracleSessionConfig],      required=True),
            ConfigField("google_calendar_config",   [GoogleCalendarConfig], required=True),
            ConfigField("google_calendar_id",       [str],      required=True),
            ConfigField("google_calendar_timezone", [str],      required=False, default="America/New_York")
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
            TelegramCommand(["system", "sys", "status", "vitals"],
                            "Reports system information.",
                            command_system),
            TelegramCommand(["lights", "light", "lumen"],
                            "Interacts with the home lights.",
                            command_lights),
            TelegramCommand(["net", "network", "wifi"],
                            "Retrieves home network info.",
                            command_network),
            #TelegramCommand(["weather", "forecast", "nimbus"],
            #                "Reports the weather.",
            #                command_weather),
            #TelegramCommand(["event", "task"],
            #                "Carries out event-specific tasks.",
            #                command_event),
            TelegramCommand(["remind", "reminder", "rem"],
                            "Sets reminders.",
                            command_remind),
            TelegramCommand(["mode", "modes", "moder"],
                            "Retrieves and sets the current house mode.",
                            command_mode),
            TelegramCommand(["calendar", "cal"],
                            "Interacts with Google Calendar.",
                            command_calendar),
            TelegramCommand(["_reset"],
                            "Resets the current chat conversation.",
                            command_s_reset,
                            secret=True),
            # ----- DEBUGGING TODO - REMOVE WHEN DONE ----- #
            TelegramCommand(["_menu"],
                            "Tests the new menu feature.",
                            command_s_menu,
                            secret=True)
            # ----- DEBUGGING TODO - REMOVE WHEN DONE ----- #
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
        
        # store converstaion IDs and timestamps in a dictionary, indexed by
        # telegram chat ID
        self.chat_conversations = {}

        # set up a menu database; generate a fitting file path if one wasn't
        # specified
        menu_db_path = self.config.bot_menu_db
        if menu_db_path is None:
            menu_db_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                        ".telegram_bot_menus.db")
        self.menu_db = MenuDatabase(menu_db_path)

        # set up a menu thread to manage the database asynchronously
        self.menu_thread = TelegramService_MenuThread(self)
        self.menu_thread.start()
    
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
            self.log.write("Message from unrecognized chat: %s" % chat_id)
            return False

        # next, check the user ID
        user_id = str(message.from_user.id)
        user_is_valid = False
        for user in self.users:
            if user.id == user_id:
                user_is_valid = True
                self.log.write("Message from %s in chat \"%s\"." %
                               (user.name, chat.name))
                break
        if not user_is_valid:
            self.log.write("Message from unrecognized user: %s" % user_id)
        return user_is_valid
    
    # Creates and returns a new OracleSession with the speaker.
    # If authentication fails, None is returned.
    def get_speaker_session(self):
        s = OracleSession(self.config.speaker)
        r = s.login()
        if not OracleSession.get_response_success(r):
            self.log.write("Failed to authenticate with speaker: %s" %
                           OracleSession.get_response_message(r))
            return None
        return s
    
    # Takes in a string message and attempts to reword it. On failure, it will
    # return the original string.
    def dialogue_reword(self, message: str):
        # attempt to connect to the speaker
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return message

        # ping the /reword endpoint
        pyld = {"message": message}
        r = speaker.post("/reword", payload=pyld)
        if OracleSession.get_response_success(r):
            # extract the response and return the reworded message
            rdata = OracleSession.get_response_json(r)
            return str(rdata["message"])
        
        # if the above didn't work, just return the original message
        self.log.write("Failed to get a reword from speaker: %s" %
                       OracleSession.get_response_message(r))
        return message
    
    # Takes in a message and communicates with DImROD's dialogue system to
    # converse with the telegram user.
    def dialogue_talk(self, message: str, conversation_id=None):
        # attempt to connect to the speaker
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker
        pyld = {"message": message}
        if conversation_id is not None:
            pyld["conversation_id"] = conversation_id
        
        # ping the /talk endpoint
        r = speaker.post("/talk", payload=pyld)
        if OracleSession.get_response_success(r):
            # extract the response and return response message
            rdata = OracleSession.get_response_json(r)
            convo_id = None if "conversation_id" not in rdata else str(rdata["conversation_id"])
            return (convo_id, str(rdata["response"]))
        
        # if the above didn't work, just return the original message
        self.log.write("Failed to get conversation from speaker: %s" %
                       OracleSession.get_response_message(r))
        return (None, None)
    
    # ------------------------------ Messaging ------------------------------- #
    # Wrapper for sending a message.
    def send_message(self, chat_id, message,
                     parse_mode=None,
                     reply_markup=None):
        # modify the message, if necessary, before sending
        if parse_mode is not None and parse_mode.lower() == "html":
            # adjust hyperlinks
            url_starts = ["http://", "https://"]
            for url_start in url_starts:
                # find all string indexes where URLs begin
                idxs = [m.start() for m in re.finditer(url_start, message)]
                for idx in idxs:
                    # for each index, insert a "<a>" and "</a>" at the front and
                    # end of the string
                    message = message[:idx] + "<a>" + message[idx:]
                    end_idx = idx + len(message[idx:].split()[0])
                    message = message[:end_idx] + "</a>" + message[end_idx:]

        # try sending the message a finite number of times
        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.send_message(chat_id, message,
                                             parse_mode=parse_mode,
                                             reply_markup=reply_markup)
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to send message. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to send message. Giving up.")
    
    # Wrapper for deleting an existing message.
    def delete_message(self, chat_id, message_id):
        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.delete_message(chat_id, message_id)
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to delete message. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to delete message. Giving up.")

    # Builds and sends a menu of buttons.
    def send_menu(self, chat_id, m: Menu,
                  parse_mode=None):
        markup = m.get_markup()
        msg = self.send_message(chat_id, m.title,
                                parse_mode=parse_mode,
                                reply_markup=markup)
        
        # perform a few sanity checks
        assert msg.reply_markup is not None
        assert type(msg.reply_markup) == InlineKeyboardMarkup
        assert len(msg.reply_markup.keyboard) == len(m.options)

        # attach the telegram message we just sent to the menu object, and save
        # it to the database
        m.set_telegram_message(msg)
        self.menu_db.save_menu(m)
        self.log.write("Add menu to database (ID: %s)" % m.get_id())

        return m
    
    # Updates the menu for an existing message.
    def update_menu(self, chat_id, message_id, m: Menu = None):
       # try updating the message's menu a finite number of times
        for i in range(self.config.bot_error_retry_attempts):
            try:
                # if `None` was given for the menu, we'll remove the menu from
                # the message by passing in `None`. Otherwise, we'll use the
                # `Menu` object to generate a markup object
                markup = None
                if m is not None:
                    markup = m.get_markup()

                return self.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup
                )
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to update menu. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to update menu. Giving up.")
    
    # ----------------------------- Bot Behavior ----------------------------- #
    # Main runner function.
    def run(self):
        super().run()

        # Generic message handler.
        @self.bot.message_handler()
        def bot_handle_message(message):
            if not self.check_message(message):
                return
            now = datetime.now()

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
            # message to dimrod. First, look for an existing conversation object
            # for this specific chat. If one exists, AND it hasn't been too long
            # since it was last touched, we'll use it
            convo_id = None
            chat_id = str(message.chat.id)
            if chat_id in self.chat_conversations:
                timediff = now.timestamp() - self.chat_conversations[chat_id]["timestamp"].timestamp()
                if timediff < self.config.bot_conversation_timeout:
                    convo_id = self.chat_conversations[chat_id]["conversation_id"]
                else:
                    self.log.write("Conversation for chat \"%s\" has expired." % chat_id)

            # next, pass the message (and conversation ID, if we found one) to
            # the dialogue interface
            try:
                (convo_id, response) = self.dialogue_talk(message.text, conversation_id=convo_id)
                # check for failure-to-converse and update the chat dictionary,
                # if able
                if response is None:
                    response = "Sorry, I couldn't generate a response."
                if convo_id is not None:
                    self.chat_conversations[chat_id] = {
                        "conversation_id": convo_id,
                        "timestamp": datetime.now()
                    }
                
                # send the message
                self.send_message(message.chat.id, response)
            except Exception as e:
                self.send_message(message.chat.id,
                                  "I'm not sure what you mean. Try /help.")
                raise e


        # Callback for any menu buttons that are pressed.
        @self.bot.callback_query_handler(func=lambda call: True)
        def menu_button_callback(call):
            menu_option_id = call.data

            # query the database for a menu option with the matching ID
            op_info = self.menu_db.search_menu_option(menu_option_id)
            if op_info is None:
                self.log.write("Unknown menu option selected.")
                return

            # with the menu option retrieve, query for the menu that owns this
            # menu option
            m = self.menu_db.search_menu(op_info.menu_id)
            if m is None:
                self.log.write("Menu option belongs to an unknown menu.")
                return

            # because the above `MenuOption` object was recreated from a
            # database entry, (and so was the `Menu` object), we want to get a
            # reference to the menu's version of the `MenuOption` object,
            # instead of the one reconstructed from the database entry.
            #
            # Why? Because we will write the `Menu` back out to the database,
            # which means *its* `MenuOption` object will be the one written out
            # to disk. This means that all modifications to the menu option
            # need to be applied to the `Menu`'s `MenuOption` object.
            op = m.get_option(op_info.get_id())

            # update the menu option to increment its selection counter
            op.select()
            self.log.write("Menu option (ID: %s) from Menu (ID %s) "
                           "was selected. (count: %d)" %
                           (op.get_id(), m.get_id(), op.selection_count))

            # write the updated menu back out to the database
            self.menu_db.save_menu(m)

        # start the bot and set it to poll periodically for updates (catch
        # errors and restart when necessary)
        while True:
            try:
                self.log.write("Beginning to poll Telegram API...")
                self.bot.polling()
            except Exception as e:
                self.log.write("Polling failed:")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.log.write("Waiting for a short time and restarting...")
                time.sleep(5)

# A class instantiated by the main `TelegramService` class whose job is to
# routinely examine and prune the database of Telegram menus.
class TelegramService_MenuThread(threading.Thread):
    def __init__(self, service: TelegramService):
        threading.Thread.__init__(self, target=self.run)
        self.service = service
    
    # Main runner function for the thread.
    def run(self):
        while True:
            now = datetime.now()

            # get all menus whose death time has passed the current time
            dead_menus = self.service.menu_db.search_menu_by_condition(
                "death_time < %f" % now.timestamp()
            )
            for m in dead_menus:
                self.service.log.write("Menu (ID: %s) is dead. "
                                       "Deleting from database." %
                                       m.get_id())
                self.service.menu_db.delete_menu(m.get_id())

                # delete the message
                self.service.delete_message(m.telegram_msg_info.chat.id,
                                            m.telegram_msg_info.id)

            # sleep for the configured time
            time.sleep(self.service.config.bot_menu_db_refresh_rate)



# ============================== Service Oracle ============================== #
class TelegramOracle(Oracle):
    # Helper function used to determine what chat ID to use when sending a
    # message in the below endpoint handlers.
    def resolve_chat_id(self, jdata: dict):
        # look for a "chat" object in the JSON data
        chat_id = None
        if "chat" in jdata:
            try:
                chat = TelegramChat()
                chat.parse_json(jdata["chat"])
                chat_id = chat.id
            except Exception as e:
                return self.make_response(success=False,
                                          msg="Invalid chat data: %s" % e)

        # alternatively, look for a "user" object in the JSON data
        if chat_id is None and "user" in jdata:
            try:
                user = TelegramUser()
                user.parse_json(jdata["user"])
                chat_id = user.id
            except Exception as e:
                return self.make_response(success=False,
                                          msg="Invalid user data: %s" % e)
        return chat_id

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
            
            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # send the message and respond
            self.service.send_message(chat_id, flask.g.jdata["message"], parse_mode="HTML")
            return self.make_response(msg="Message sent successfully.")

        # Endpoint used to instruct the bot to send a message with a menu (a
        # series of buttons) attached..
        @self.server.route("/bot/send/menu", methods=["POST"])
        def endpoint_bot_send_menu():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # look for a "menu" field in the JSON data
            if "menu" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No menu provided.")

            # attempt to parse the JSON representing the menu
            menu = Menu()
            try:
                menu.parse_json(flask.g.jdata["menu"])
            except:
                return self.make_response(success=False,
                                          msg="Invalid menu data.")
            
            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # send the menu and respond (return the menu object)
            self.service.send_menu(chat_id, menu, parse_mode="HTML")
            return self.make_response(msg="Menu sent successfully.",
                                      payload=menu.to_json())
        
        # Endpoint used to retrieve information about an existing menu.
        @self.server.route("/bot/get/menu", methods=["POST"])
        def endpoint_bot_get_menu():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # look for a "menu_id" field in the JSON data
            if "menu_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No menu ID provided.")
            menu_id = str(flask.g.jdata["menu_id"])
            
            # search for the menu in the database; return early if it can't be
            # found
            m = self.menu_db.search_menu(menu_id)
            if m is None:
                return self.make_response(success=False,
                                          msg="Unknown menu ID.")

            # return the menu as a JSON object in the response payload
            return self.make_response(payload=m.to_json())


# =============================== Runner Code ================================ #
cli = ServiceCLI(config=TelegramConfig, service=TelegramService, oracle=TelegramOracle)
cli.run()

