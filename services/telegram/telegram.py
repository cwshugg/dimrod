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
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, \
                          ReactionTypeEmoji
import traceback
import threading
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConversation, DialogueMessage, \
                         DialogueAuthor, DialogueAuthorType
from lib.google.google_calendar import GoogleCalendarConfig
from lib.ynab import YNABConfig
from lib.news import NewsAPIConfig, NewsAPIQueryArticles

# Service imports
from telegram_objects import TelegramChat, TelegramUser
from menu import Menu, MenuDatabase, MenuBehaviorType
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
from commands.budget import command_budget
from commands.news import command_news
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
            ConfigField("bot_menu_db_refresh_rate", [int],      required=False, default=60),
            ConfigField("lumen",    [OracleSessionConfig],      required=True),
            ConfigField("warden",   [OracleSessionConfig],      required=True),
            ConfigField("notif",    [OracleSessionConfig],      required=True),
            ConfigField("moder",    [OracleSessionConfig],      required=True),
            ConfigField("speaker",  [OracleSessionConfig],      required=True),
            ConfigField("google_calendar_config",   [GoogleCalendarConfig], required=True),
            ConfigField("google_calendar_id",       [str],      required=True),
            ConfigField("google_calendar_timezone", [str],      required=False, default="America/New_York"),
            ConfigField("ynab",     [YNABConfig],               required=True),
            ConfigField("news",     [NewsAPIConfig],            required=True),
            ConfigField("news_default_queries", [NewsAPIQueryArticles], required=True)
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
            TelegramCommand(["help", "h", "commands", "what"],
                            "Presents this help menu.",
                            command_help),
            TelegramCommand(["system", "sys", "s"],
                            "Reports system information.",
                            command_system),
            TelegramCommand(["lights", "light", "lumen", "l"],
                            "Interacts with the home lights.",
                            command_lights),
            TelegramCommand(["net", "network", "n"],
                            "Retrieves home network info.",
                            command_network),
            TelegramCommand(["remind", "reminder", "rem", "r"],
                            "Sets reminders.",
                            command_remind),
            TelegramCommand(["mode", "modes", "moder"],
                            "Retrieves and sets the current house mode.",
                            command_mode),
            TelegramCommand(["calendar", "cal", "c"],
                            "Interacts with Google Calendar.",
                            command_calendar),
            TelegramCommand(["budget", "bud", "b"],
                            "Interacts with the Budget.",
                            command_budget),
            TelegramCommand(["news", "articles", "headlines"],
                            "Retrieves news articles to read.",
                            command_news),
            TelegramCommand(["_reset"],
                            "Resets the current chat conversation.",
                            command_s_reset,
                            secret=True),
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

    # Performs a oneshot LLM call and response.
    def dialogue_oneshot(self, intro: str, message: str):
        # attempt to connect to the speaker
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return message

        # ping the /oneshot endpoint
        pyld = {"intro": intro, "message": message}
        r = speaker.post("/oneshot", payload=pyld)
        if OracleSession.get_response_success(r):
            # extract the response and return the reworded message
            rdata = OracleSession.get_response_json(r)
            return str(rdata["message"])

        # if the above didn't work, just return the original message
        self.log.write("Failed to get a oneshot response from speaker: %s" %
                       OracleSession.get_response_message(r))
        return message

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
    def dialogue_talk(self, message: any, conversation_id=None):
        # attempt to connect to the speaker
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker
        pyld = {"message": message.text}
        if conversation_id is not None:
            pyld["conversation_id"] = conversation_id

        # include telegram message information
        message_info = {
            "message_id": str(message.message_id),
            "chat_id": str(message.chat.id),
        }
        pyld["telegram_message"] = message_info

        # ping the /talk endpoint
        r = speaker.post("/talk", payload=pyld)
        if OracleSession.get_response_success(r):
            return OracleSession.get_response_json(r)

        # if the above didn't work, return nothing
        self.log.write("Failed to get conversation from speaker: %s" %
                       OracleSession.get_response_message(r))
        return None

    # Searches for a message object and returns a list of matches.
    def dialogue_message_search(self,
                                message_id=None,
                                telegram_message_id=None,
                                telegram_chat_id=None):
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker
        pyld = {}
        if message_id is not None:
            pyld["message_id"] = message_id
        if telegram_message_id is not None:
            pyld["telegram_message_id"] = telegram_message_id
        if telegram_chat_id is not None:
            pyld["telegram_chat_id"] = telegram_chat_id

        # ping the /message/search endpoint
        r = speaker.post("/message/search", payload=pyld)
        if OracleSession.get_response_success(r):
            return OracleSession.get_response_json(r)

        # if the above didn't work, return nothing
        self.log.write("Failed to search messages from speaker: %s" %
                       OracleSession.get_response_message(r))
        return None

    # Updates the message pointed at by `message_id` with the given parameters.
    def dialogue_message_update(self,
                                message_id: str,
                                telegram_message_id=None,
                                telegram_chat_id=None):
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker
        pyld = {"message_id": message_id}
        if telegram_message_id is not None:
            pyld["telegram_message_id"] = telegram_message_id
        if telegram_chat_id is not None:
            pyld["telegram_chat_id"] = telegram_chat_id

        # ping the /message/search endpoint
        r = speaker.post("/message/update", payload=pyld)
        if OracleSession.get_response_success(r):
            return OracleSession.get_response_json(r)

        # if the above didn't work, return nothing
        self.log.write("Failed to search messages from speaker: %s" %
                       OracleSession.get_response_message(r))
        return None

    # Creates a new conversation.
    def dialogue_conversation_create(self, convo: DialogueConversation):
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker and ping the
        # `/conversation/create` endpoint
        pyld = {"conversation": convo.to_json()}
        r = speaker.post("/conversation/create", payload=pyld)
        if OracleSession.get_response_success(r):
            return OracleSession.get_response_json(r)

        # if the above didn't work, return nothing
        self.log.write("Failed to create a conversation with speaker: %s" %
                       OracleSession.get_response_message(r))
        return None

    def dialogue_conversation_addmsg(self, convo_id: str, msg: DialogueMessage):
        speaker = self.get_speaker_session()
        if speaker is None:
            self.log.write("Failed to connect to the speaker.")
            return (None, None)

        # build a payload to pass to the speaker and ping the
        # `/conversation/addmsg` endpoint
        pyld = {
            "conversation_id": convo_id,
            "message": msg.to_json()
        }
        r = speaker.post("/conversation/addmsg", payload=pyld)
        if OracleSession.get_response_success(r):
            return OracleSession.get_response_json(r)

        # if the above didn't work, return nothing
        self.log.write("Failed to add a message to conversation \"%s\" with speaker: %s" %
                       (convo_id,
                       OracleSession.get_response_message(r)))
        return None

    # ------------------------------ Messaging ------------------------------- #
    # Helper function for properly formatting and sanitizing text to be used in
    # a Telegram message.
    def sanitize_message_text(self, text: str, parse_mode=None):
        if parse_mode is None:
            return text

        if parse_mode.lower() == "html":
            # adjust hyperlinks such that they are wrapped in HTML anchor tags
            url_starts = ["http://", "https://"]
            for url_start in url_starts:
                # find all string indexes where URLs begin
                idxs = [m.start() for m in re.finditer(url_start, text)]
                for idx in idxs:
                    # for each index, insert a "<a>" and "</a>" at the front and
                    # end of the string
                    text = text[:idx] + "<a>" + text[idx:]
                    end_idx = idx + len(text[idx:].split()[0])
                    text = text[:end_idx] + "</a>" + text[end_idx:]

        return text

    # Wrapper for sending a message.
    def send_message(self, chat_id, text,
                     parse_mode=None,
                     reply_markup=None):
        text = self.sanitize_message_text(text, parse_mode=parse_mode)

        # try sending the message a finite number of times
        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.send_message(chat_id, text,
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
        return None

    # Wrapper for updating a message's text.
    def update_message(self, chat_id, message_id,
                       new_text: str,
                       parse_mode=None):
        new_text = self.sanitize_message_text(new_text, parse_mode=parse_mode)

        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.edit_message_text(new_text,
                                                  chat_id=chat_id,
                                                  message_id=message_id)
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to update message. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to update message. Giving up.")

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

    # Sends a message that is intended to pose a question (and ask for a
    # response from) the user. The message is sent, and a conversation is sent
    # to Speaker for storage. The details of the conversation are returned, so
    # the caller can check in for a response later.
    def send_question(self, chat_id, question: str, parse_mode=None):
        # send the message, and receive the telegram message object
        message = self.send_message(chat_id, question, parse_mode=parse_mode)

        # create a `DialogueConversation` object to represent this question
        now = datetime.now()
        convo = DialogueConversation.from_json({
            "messages": [
                DialogueMessage.from_json({
                    "author": DialogueAuthor.from_json({
                        "type": DialogueAuthorType.SYSTEM_QUERY_TO_USER.value,
                        "name": "telegram_questioner",
                    }),
                    "content": question,
                    "telegram_chat_id": str(chat_id),
                    "telegram_message_id": str(message.id),
                }).to_json()
            ],
            "time_start": now.isoformat(),
            "time_latest": now.isoformat(),
            "telegram_chat_id": str(chat_id),
        })
        convo_data = self.dialogue_conversation_create(convo)

        # return the conversation object that was returned by Speaker
        return DialogueConversation.from_json(convo_data)

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

    # Removes a menu from a message.
    def remove_menu(self, chat_id, message_id):
        return self.update_menu(chat_id, message_id, m=None)

    # Adds a reaction to a message.
    def react_to_message(self, chat_id, message_id, emoji="👍", is_big=False):
        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.set_message_reaction(
                    chat_id,
                    message_id,
                    [ReactionTypeEmoji(emoji)],
                    is_big=is_big
                )
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to react to message. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to react to message. Giving up.")

    # Removes reactions from a message.
    def remove_message_reactions(self, chat_id, message_id):
        for i in range(self.config.bot_error_retry_attempts):
            try:
                return self.bot.set_message_reaction(
                    chat_id,
                    message_id,
                    []
                )
            except Exception as e:
                # on failure, sleep for a small amount of time, and get a new
                # bot instance
                self.log.write("Failed to remove message reactions. "
                               "Resetting the bot, sleeping for a short time, "
                               "and trying again.")
                tb = traceback.format_exc()
                for line in tb.split("\n"):
                    self.log.write(line)
                self.refresh()
                time.sleep(self.config.bot_error_retry_delay)
        self.log.write("Failed to remove message reactions. Giving up.")

    # ----------------------------- Bot Behavior ----------------------------- #
    # Main runner function.
    def run(self):
        super().run()

        # start up auxiliary threads
        self.menu_thread.start()

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
            # message to DImROD.

            # is this message in response to another? If so, look for a message
            # mapping for the previous message
            convo_id = None
            convo_is_system_query = False
            chat_id = str(message.chat.id)
            if hasattr(message, "reply_to_message") and \
               message.reply_to_message is not None:
                rtmsg = message.reply_to_message

                # search for a message mapped to the reply-to-message's
                # telegram message ID
                messages = self.dialogue_message_search(
                    telegram_message_id=str(rtmsg.message_id),
                )

                # if a result was found, get the conversation ID from the
                # message and save it
                if messages is not None:
                    messages_len = len(messages)
                    if messages_len > 0:
                        if messages_len > 1:
                            self.log.write("Unexpectedly found more than one message "
                                           "matching telegram message ID \"%s\". "
                                           "Using the first one." %
                                           str(rtmsg.message_id))
                        first_message_data = messages[0]
                        first_message = DialogueMessage.from_json(first_message_data["message"])
                        convo_id = first_message_data["conversation_id"]

                        # is the message a system query type?
                        if first_message.author.type in [DialogueAuthorType.SYSTEM_QUERY_TO_USER,
                                                         DialogueAuthorType.USER_ANSWER_TO_QUERY]:
                            convo_is_system_query = True
                        # if the message is a normal message, we want to
                        # replace the conversation ID in the local conversation
                        # record, such that this particular telegram chat's
                        # current conversation is replaced with this one
                        else:
                            self.chat_conversations[chat_id] = {
                                "conversation_id": convo_id,
                                "timestamp": datetime.now()
                            }
            # otherwise, look for an active conversation ID for this telegram
            # chat, and use that conversation ID instead
            else:
                chat_id = str(message.chat.id)
                if chat_id in self.chat_conversations:
                    timediff = now.timestamp() - self.chat_conversations[chat_id]["timestamp"].timestamp()
                    if timediff < self.config.bot_conversation_timeout:
                        convo_id = self.chat_conversations[chat_id]["conversation_id"]
                    else:
                        self.log.write("Conversation for chat \"%s\" has expired." % chat_id)

            # is the conversation a system query to the user? If so, we'll
            # simply update the conversation to include this new message, and
            # react to the message
            if convo_is_system_query:
                # put together a message object to use to update the system
                # query conversation
                answer_msg = DialogueMessage.from_json({
                    "author": DialogueAuthor.from_json({
                        "type": DialogueAuthorType.USER_ANSWER_TO_QUERY.value,
                        "name": "telegram_answerer",
                    }),
                    "content": message.text,
                    "telegram_chat_id": str(chat_id),
                    "telegram_message_id": str(message.id),
                })
                self.dialogue_conversation_addmsg(convo_id, answer_msg)

                # add a reaction to the message, so the user knows we processed
                # the message
                self.react_to_message(chat_id, message.id, emoji="👍")
                return

            # if it's a normal message, pass the message (and conversation ID,
            # if we found one) to the dialogue interface
            try:
                talkdata = self.dialogue_talk(message, conversation_id=convo_id)

                # check for failure-to-converse and update the chat dictionary,
                # if able
                if talkdata is None:
                    response = "Sorry, I couldn't generate a response."

                response = talkdata["response"]

                # send the response, and capture the returned message object
                rmessage = self.send_message(message.chat.id, response, parse_mode="HTML")
                if rmessage is None:
                    raise Exception("Failed to send response message.")

                if "conversation_id" in talkdata:
                    # update both the request message (the user's message) and
                    # the response message (the message we generated) to
                    # includethe telegram chat ID and their corresponding
                    # telegram message IDs. This will let us query for them
                    # later by the telegram message ID
                    self.dialogue_message_update(
                        talkdata["request_message_id"],
                        telegram_message_id=str(message.id),
                        telegram_chat_id=str(message.chat.id)
                    )
                    self.dialogue_message_update(
                        talkdata["response_message_id"],
                        telegram_message_id=str(rmessage.id),
                        telegram_chat_id=str(rmessage.chat.id)
                    )

                    # add the conversation record to the local, temporary
                    # conversation table (this is used to track, and timeout,
                    # active conversations)
                    self.chat_conversations[chat_id] = {
                        "conversation_id": talkdata["conversation_id"],
                        "timestamp": datetime.now()
                    }

            except Exception as e:
                # dump the exception stack trace into the message, for easier
                # debugging through Telegram
                tb = traceback.format_exc()
                msg = "Something went wrong.\n\n<code>%s</code>" % tb
                self.send_message(message.chat.id, msg, parse_mode="HTML")

                # raise the exception
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

            # next, look at the menu's behavior type. We'll adjust the menu
            # options differently depending on the selecteed value
            original_op_titles = [o.title for o in m.options]
            do_menu_update = False
            if m.behavior_type == MenuBehaviorType.ACCUMULATE:
                # if we're accumulating, our job is easy; just increment the
                # option that was selected
                op.select_add()
                do_menu_update = True

                # iterate through all options and update the corresponding
                # button on the menu to show the number of times it was
                # selected (only do so if it's selection count is non-zero)
                for o in m.options:
                    if o.selection_count == 0:
                        continue
                    o.title = "%s [%d]" % (o.title, o.selection_count)
            if m.behavior_type == MenuBehaviorType.MULTI_CHOICE:
                # if the selected option was already selected, we'll reset it
                if op.selection_count == 1:
                    op.select_set(0)
                else:
                    op.select_set(1)
                do_menu_update = True

                # iterate through all options and update the corresponding
                # button on the menu to show which ones have been selected
                for o in m.options:
                    if o.selection_count == 0:
                        continue
                    o.title = "%s ✅" % o.title
            elif m.behavior_type == MenuBehaviorType.SINGLE_CHOICE:
                # if the menu only allows a single choice, we need to set the
                # current option's selection count to 1, and reduce all others
                # to zero

                # if the selected option was already selected, we'll reset it
                # (i.e. 1 --> 0 and 0 --> 1)
                new_value = 0 if op.selection_count == 1 else 1
                do_menu_update = True

                # zero out all options and set the seleted option's new value
                for o in m.options:
                    o.select_set(0)
                op.select_set(new_value)

                # set the select option's title to show that it was the one
                # chosen value, if its new value is 1
                if new_value == 1:
                    op.title = "%s ✅" % op.title

            # apply any changes made above to the option titles (the text on
            # the buttons) to the Telegram menu
            if do_menu_update:
                self.update_menu(m.telegram_msg_info.chat.id,
                                 m.telegram_msg_info.id,
                                 m)
            else:
                # otherwise, change a single button twice, briefly, to force
                # telegram to get rid of the shimmery "a button was just
                # pressed" effect
                for text in [" %s " % op.title, op.title]:
                    op.title = text
                    self.update_menu(m.telegram_msg_info.chat.id,
                                     m.telegram_msg_info.id,
                                     m)

            # update the menu option to increment its selection counter
            self.log.write("Menu option (ID: %s) from Menu (ID %s) "
                           "was selected. (count: %d)" %
                           (op.get_id(), m.get_id(), op.selection_count))

            # write the updated menu back out to the database (first, reset the
            # option titles to reflect the original versions, before we updated
            # the Telegram menu, so the database retains the original,
            # un-modified titles)
            for (i, o) in enumerate(m.options):
                o.title = original_op_titles[i]
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
        if "chat" in jdata:
            try:
                chat = TelegramChat()
                chat.parse_json(jdata["chat"])
                return chat.id
            except Exception as e:
                return self.make_response(success=False,
                                          msg="Invalid chat data: %s" % e)

        # look for a standalone "chat_id" field in the JSON data
        if "chat_id" in jdata:
            return str(jdata["chat_id"])

        # alternatively, look for a "user" object in the JSON data
        if "user" in jdata:
            try:
                user = TelegramUser()
                user.parse_json(jdata["user"])
                return user.id
            except Exception as e:
                return self.make_response(success=False,
                                          msg="Invalid user data: %s" % e)

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
        @self.server.route("/bot/send/message", methods=["POST"])
        def endpoint_bot_send_message():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # look for a "text" field in the JSON data
            if "text" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message text provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # parse the parse mode (optional)
            pmode = "HTML"
            if "parse_mode" in flask.g.jdata:
                pmode = str(flask.g.jdata["parse_mode"])

            # send the message and respond
            self.service.send_message(chat_id, flask.g.jdata["text"], parse_mode=pmode)
            return self.make_response(msg="Message sent successfully.")

        # Endpoint used to instruct the bot to update a message.
        @self.server.route("/bot/update/message", methods=["POST"])
        def endpoint_bot_update_message():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

            # look for a "text" field in the JSON data
            if "text" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message text provided.")

            # send the message and respond
            self.service.update_message(chat_id,
                                        flask.g.jdata["message_id"],
                                        flask.g.jdata["text"],
                                        parse_mode="HTML")
            return self.make_response(msg="Message updated successfully.")

        # Endpoint used to instruct the bot to update a message's reactions.
        @self.server.route("/bot/update/message/reaction", methods=["POST"])
        def endpoint_bot_update_message_reaction():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

            # look for an "emoji" field in the JSON data
            if "emoji" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No reaction emoji provided.")

            # if "is_big" is present, interpret is as a boolean
            is_big_reaction = False
            if "is_big" in flask.g.jdata:
                is_big_reaction = bool(flask.g.jdata["is_big"])

            # set the message's reaction
            self.service.react_to_message(chat_id,
                                          flask.g.jdata["message_id"],
                                          emoji="👍",
                                          is_big=is_big_reaction)
            return self.make_response(msg="Message reaction set successfully.")

        # Endpoint used to instruct the bot to delete a message.
        @self.server.route("/bot/delete/message", methods=["POST"])
        def endpoint_bot_delete_message():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

            # send the message and respond
            self.service.delete_message(chat_id,
                                        flask.g.jdata["message_id"])
            return self.make_response(msg="Message deleted successfully.")

        # Endpoint used to instruct the bot to delete a message's reactions.
        @self.server.route("/bot/delete/message/reaction", methods=["POST"])
        def endpoint_bot_delete_message_reaction():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

            # remove reactions from the message
            self.service.remove_message_reactions(chat_id,
                                                  flask.g.jdata["message_id"])
            return self.make_response(msg="Message reactions deleted successfully.")

        # Endpoint used to instruct the bot to send a message.
        @self.server.route("/bot/send/question", methods=["POST"])
        def endpoint_bot_send_question():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # look for a "text" field in the JSON data
            if "text" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message text provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # parse the parse mode (optional)
            pmode = "HTML"
            if "parse_mode" in flask.g.jdata:
                pmode = str(flask.g.jdata["parse_mode"])

            # send the message and respond
            convo = self.service.send_question(
                chat_id,
                flask.g.jdata["text"],
                parse_mode=pmode
            )
            return self.make_response(payload=convo.to_json())

        # Endpoint used to instruct the bot to send a message with a menu (a
        # series of buttons) attached.
        @self.server.route("/bot/send/menu", methods=["POST"])
        def endpoint_bot_send_menu():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

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

            # send the menu and respond (return the menu object)
            self.service.send_menu(chat_id, menu, parse_mode="HTML")
            return self.make_response(msg="Menu sent successfully.",
                                      payload=menu.to_json())

        # Endpoint used to instruct the bot to update a menu.
        @self.server.route("/bot/update/menu", methods=["POST"])
        def endpoint_bot_update_menu():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

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

            # send the menu and respond (return the menu object)
            self.service.update_menu(chat_id,
                                     flask.g.jdata["message_id"],
                                     menu)
            return self.make_response(msg="Menu updated successfully.",
                                      payload=menu.to_json())

        # Endpoint that removes a menu from a message.
        @self.server.route("/bot/remove/menu", methods=["POST"])
        def endpoint_bot_remove_menu():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            if not flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No JSON data provided.")

            # make sure we have a chat ID to work with
            chat_id = self.resolve_chat_id(flask.g.jdata)
            if chat_id is None:
                return self.make_response(success=False,
                                          msg="No chat or user provided.")

            # look for a "message_id" field in the JSON data
            if "message_id" not in flask.g.jdata:
                return self.make_response(success=False,
                                          msg="No message ID provided.")

            # send the menu and respond (return the menu object)
            self.service.remove_menu(chat_id,
                                     flask.g.jdata["message_id"])
            return self.make_response(msg="Menu removed successfully.")

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
            m = self.service.menu_db.search_menu(menu_id)
            if m is None:
                return self.make_response(success=False,
                                          msg="Unknown menu ID.")

            # return the menu as a JSON object in the response payload
            return self.make_response(payload=m.to_json())


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=TelegramConfig, service=TelegramService, oracle=TelegramOracle)
    cli.run()

