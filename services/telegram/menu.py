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
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ========================== Generic Parent Objects ========================== #
# A generic parent-level object used to specify a config for a menu or a menu
# option. This is needed because the two sub-objects have a somewhat circular
# dependency on one another.

class MenuObjectConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],              required=False, default=None),
            ConfigField("children", [MenuObjectConfig], required=True)
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

# A generic object used to represent either a menu or a menu option. This is
# needed because the two sub-objects have a somewhat circular dependency on one
# another.
class MenuObject:
    def __init__(self, config: MenuObjectConfig):
        self.config = config


# ================================= Configs ================================== #
# Config object used to create a MenuOption object.
class MenuOptionConfig(MenuObjectConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("title",    [str],              required=True),
        ]

# Config object used to create a Menu object.
class MenuConfig(MenuObjectConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("title",    [str],              required=True),
        ]


# ================================= Objects ================================== #
# A class representing a single answer option for a menu prompt. Each
# MenuOption can have a list of child objects, which allows a tree of menus to
# be built.
class MenuOption(MenuObject):
    def __init__(self, config: MenuOptionConfig):
        super().__init__(config)
    
    # Adds a child menu to this option.
    def add_submenu(self, m: MenuConfig):
        self.children.append(m)

    # Generates a Telegram Bot button and returns it.
    def get_button(self):
        return InlineKeyboardButton(self.config.title,
                                    callback_data=self.config.get_id())

# A class representing a menu with `MenuOption` objects used as
# possible responses.
class Menu(MenuObject):
    def __init__(self, config: MenuConfig):
        super().__init__(config)
    
    # Adds a new menu option to the menu.
    def add_option(self, op: MenuOptionConfig):
        self.children.append(op)
    
    # Generates a Telegram Bot markup, with all options included as buttons,
    # and returns it.
    def get_markup(self, buttons_per_row=2):
        markup = InlineKeyboardMarkup()
        markup.row_width = buttons_per_row

        # add all options as buttons
        assert len(self.config.children) > 0
        for op in self.config.children:
            mop = MenuOption(op)
            markup.add(mop.get_button())

        return markup

