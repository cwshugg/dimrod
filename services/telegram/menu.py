# This module defines an interface with which the Telegram bot can poll the
# user with a menu interface in a tree-like structure.
# 
# My hope with this is to give the bot the ability to throw questions my way to
# help get some stuff off my mind. By answering the bot's questions, it should
# then be able to go and trigger certain actions that I might otherwise need to
# do manually. Things like:
#
# * "Are you out of milk?"
# * "Did you do SOME_TASK yet?"
#
# This can also be used for menu navigations.

# Imports
import os
import sys
import hashlib
from datetime import datetime
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import json

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# Service imports
from telegram_objects import TelegramMessage, TelegramButton


# ========================== Generic Parent Objects ========================== #
# A generic parent-level object used to specify a config for a menu or a menu
# option. This is needed because the two sub-objects have a somewhat circular
# dependency on one another.

class MenuObject(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",           [str],      required=False, default=None),
        ]
    
    # Overloaded JSON parsing function from parent.
    def parse_json(self, jdata: dict):
        super().parse_json(jdata)
        self.get_id()

    # Generates and returns a unique ID for this option.
    def get_id(self):
        if self.id is None:
            data = str(id(self)).encode("utf-8") + bytes(os.urandom(16))
            self.id = hashlib.sha256(data).hexdigest()
        return self.id


# ================================= Objects ================================== #
# Config object used to create a MenuOption object.
class MenuOption(MenuObject):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("title",            [str],      required=True),
            ConfigField("menu_id",          [str],      required=False, default=""),
            ConfigField("kills_menu",       [bool],     required=False, default=True),
            ConfigField("selection_count",  [int],      required=False, default=0),
        ]

    # Generates a Telegram Bot button and returns it.
    def get_button(self):
        return InlineKeyboardButton(self.title,
                                    callback_data=self.get_id())
    
    # Stores the menu option's corresponding button object.
    def set_telegram_button(self, btn: telebot.types.InlineKeyboardButton):
        self.telegram_btn_info = TelegramButton.from_telegram_to_obj(btn)
    
    # This should be called when the menu option is selected by the user.
    def select(self):
        self.selection_count += 1
    
# Config object used to create a Menu object.
class Menu(MenuObject):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("title",        [str],      required=True),
            ConfigField("options",      [MenuOption], required=True),
            ConfigField("timeout",      [int],      required=False, default=86400),
            ConfigField("birth_time",   [datetime], required=False, default=None),
            ConfigField("death_time",   [datetime], required=False, default=None),
            ConfigField("telegram_msg_info", [TelegramMessage],      required=False, default=None),
        ]

    # Overridden `parse_json()`.
    def parse_json(self, jdata: dict):
        super().parse_json(jdata)
        if self.birth_time is None:
            self.birth_time = datetime.now()
        if self.death_time is None:
            self.death_time = dtu.add_seconds(self.birth_time, self.timeout)

        # for each of the MenuOption objects, set its menu ID to equal this
        # menu's ID
        for op in self.options:
            op.menu_id = self.get_id()

    # Stores the menu's corresponding message object.
    def set_telegram_message(self, msg: telebot.types.Message):
        self.telegram_msg_info = TelegramMessage.from_telegram_to_obj(msg)
    
    # Generates a Telegram Bot markup, with all options included as buttons,
    # and returns it.
    def get_markup(self, buttons_per_row=2):
        markup = InlineKeyboardMarkup(row_width=buttons_per_row)

        # add all options as buttons
        assert len(self.options) > 0
        for op in self.options:
            markup.add(op.get_button())
        return markup
    
    # Examines the menu's "birth time" against the current time, and returns
    # True if it has exceeded its timeout time.
    def is_expired(self, now=None):
        now = datetime.now() if now is None else now
        return dtu.diff_in_seconds(now, self.birth_time) > self.timeout
    
    # Examines the menu's various options and determines if it has been "dead",
    # i.e. all used up and no longer available for interaction.
    def is_dead(self):
        # if the menu has already died, return early
        if self.death_time is not None:
            return True

        # otherwise, examine all menu options
        for op in self.options:
            # if any option that is selected multiple times is configured to
            # "kill" the menu upon selection, then we should deem the menu dead
            if op.selection_count > 0 and op.kills_menu:
                return True
        return False
    

# ============================= Menu Databasing ============================== #
# Implements an SQLite3 database to store Telegram menus and buttons.
class MenuDatabase:
    def __init__(self, path: str):
        self.db_path = path
        self.visible_fields_menu_option = ["id", "menu_id"]
        self.visible_fields_menu = ["id"]
    
    # Saves a Menu option to the database.
    def save_menu_option(self, op: MenuOption, connection=None):
        # establish a connection, if one wasn't already passed in
        connection_was_provided = connection is not None
        if not connection_was_provided:
            connection = sqlite3.connect(self.db_path)
        
        # open a cursor
        cur = connection.cursor()

        # make sure the option table exists
        cur.execute(op.get_sqlite3_table_definition(
            "menu_options",
            fields_to_keep_visible=self.visible_fields_menu_option
        ))
        
        cur.execute("INSERT OR REPLACE INTO menu_options VALUES %s" %
                    str(op.to_sqlite3(fields_to_keep_visible=self.visible_fields_menu_option)))

        # commit and close (only if the connection wasn't provided)
        connection.commit()
        if not connection_was_provided:
            connection.close()
    
    # Saves a menu, and all of its options, into the database.
    def save_menu(self, m: Menu, connection=None):
        # establish a connection, if one wasn't already passed in
        connection_was_provided = connection is not None
        if not connection_was_provided:
            connection = sqlite3.connect(self.db_path)

        # open a cursor into the database
        cur = connection.cursor()

        # make sure the option table exists
        cur.execute(m.get_sqlite3_table_definition(
            "menus",
            fields_to_keep_visible=self.visible_fields_menu
        ))
        
        # insert the menu into the database
        cur.execute("INSERT OR REPLACE INTO menus VALUES %s" %
                    str(m.to_sqlite3(fields_to_keep_visible=self.visible_fields_menu)))

        # next, examine the menu's options; add each to the menu option table
        for op in m.options:
            self.save_menu_option(op, connection=connection)
        
        # commit and close (only if the connection wasn't provided)
        connection.commit()
        if not connection_was_provided:
            connection.close()
    
    # Performs a generic result and returns the SQLite3 result.
    def search(self, table: str, condition: str):
        # construct a condition string
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # connect, query, and return
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        result = cur.execute(cmd)
        return result

    # Searches for a menu option by its ID and returns it if found. Returns
    # None if no match is found.
    def search_menu_option(self, option_id: str):
        # return early if the database doesn't exist
        if not os.path.isfile(self.db_path):
            return None

        # build a partial condition string and pass it to the helper function.
        # Interpret the first returned "row" as the menu option and return the
        # reconstructed object
        cond = "id == \"%s\"" % option_id
        for row in self.search("menu_options", cond):
            op = MenuOption()
            op.parse_sqlite3(
                row,
                fields_kept_visible=self.visible_fields_menu_option
            )
            return op
        return None
    
    # Searches for a menu by its ID and returns it if found. Returns None if no
    # match is found.
    def search_menu(self, menu_id: str):
        # return early if the database doesn't exist
        if not os.path.isfile(self.db_path):
            return None

        # build a partial condition string and pass it to the helper function.
        # Interpret the first returned "row" as the menu option and return the
        # reconstructed object
        cond = "id == \"%s\"" % menu_id
        for row in self.search("menus", cond):
            m = Menu()
            m.parse_sqlite3(
                row,
                fields_kept_visible=self.visible_fields_menu
            )
            return m
        return None
    
