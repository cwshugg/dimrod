# This module defines classes used to carry out intent-parsing-based actions
# when processing dialogue messages.

import os
import sys
import abc

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.dialogue.dialogue import DialogueConfig, DialogueInterface, \
                                  DialogueAuthor, DialogueAuthorType, \
                                  DialogueConversation, DialogueMessage


# The configuration class for a speaker action. This can be extended by
# subclasses.
class SpeakerActionConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("class_name",               [str],      required=True)
        ]

# The main class for a speaker action.
class SpeakerAction(abc.ABC):
    # Constructor. Takes in a config object.
    def __init__(self, config: SpeakerActionConfig):
        self.config = config
    
    # Builds and initializes a DialogueIntent object. This object will be used
    # to process intents in user messages in order to trigger this action.
    #
    # This must be implemented by subclasses.
    @abc.abstractmethod
    def intent_init(self):
        pass
    
    # Takes in the original user message and the list of parsed intent
    # parameters. This is the main function that should be implemented to carry
    # out an action.
    #
    # This must be implemented by subclasses.
    @abc.abstractmethod
    def run(self, msg: str, params: list):
        pass

