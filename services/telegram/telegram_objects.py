# A small module that defines class wrappers for Telegram chat and user objects.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# =============================== Chat Object ================================ #
class TelegramChat(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],      required=True),
            ConfigField("name",     [str],      required=True)
        ]


# =============================== User Object ================================ #
class TelegramUser(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",       [str],      required=True),
            ConfigField("name",     [str],      required=True)
        ]

