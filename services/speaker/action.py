# This module defines information about intents to be used by an NLP library to
# parse actions out of dialogue.

import os
import sys
import abc

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface, DialogueAuthor, \
                         DialogueAuthorType, DialogueConversation, DialogueMessage


# =============================== Config Class =============================== #
class DialogueActionConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("class_name",               [str],      required=True),
            ConfigField("confidence_threshold",     [float],    required=True)
        ]


class DialogueAction(abc.ABC):
    # Constructor. Takes in a config object.
    def __init__(self, config_data: dict):
        self.config = None # must be handled by child class

    # -------------------------- Abstract Interface -------------------------- #
    # Builds and initializes an intent parsing engine.
    @abc.abstractmethod
    def engine_init(self):
        pass
    
    # Takes in text and parses it for intent. If a valid intent is found by the
    # engine with a high enough confidence, this may carry out an action.
    @abc.abstractmethod
    def engine_process(self, text: str):
        return None

