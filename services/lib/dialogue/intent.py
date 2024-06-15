# This module implements classes used to parse user intent out of a user's
# message.

import os
import sys
import abc
import openai

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.dialogue.dialogue import DialogueConfig, DialogueConversation, \
                                  DialogueAuthor, DialogueAuthorType, \
                                  DialogueMessage


# ================================= Intents ================================== #
# A class representing information to be sent to OpenAI regarding parsing user
# intent from messages. Information from this class will be compiled into a
# summary that allows an LLM to determine what to look for in a message to
# decide if this Intent is present in the message.
class DialogueIntent(abc.ABC):
    # Constructor.
    def __init__(self, name: str):
        self.name = name
    
    # This function must be implemented by subclasses to describe to the LLM
    # what it should look for in a user's message in order for this action
    # intent to be present.
    #
    # This function should return a string. For example: "If the user makes a
    # request or instructs you to turn on the lights, then this intent is
    # present."
    @abc.abstractmethod
    def describe_recognition(self):
        pass

    # This function must be implemented by subclasses to describe the list of
    # parameters that the LLM must produce if it recognizes that this action
    # intent is present in a user's message.
    #
    # This function should return a list of `DialogueIntentParam`
    # objects.
    @abc.abstractmethod
    def describe_parameters(self):
        pass
    

# ============================== Intent Parser =============================== #
# A class that utilizies the above intent classes to parse intent from a
# message.
class DialogueIntentParser(abc.ABC):
    # Constructor. Accepts a DialogueConfig object and a list of DialogueIntent
    # objects as input.
    def __init__(self, config: DialogueConfig, intents: list):
        self.config = config
        self.intents = intents
    
    # Uses the parser's given intent objects to build a system prompt for the
    # LLM.
    def build_prompt(self):
        # provide an overview
        p = ""
        p += "Your job is to read a message and determine what intents the " \
             "author has specified by comparing the message against a list of " \
             "described intent types.\n"
        p += "Each provided intent type specifies an action the author may" \
             "want to be carried out.\n"
        p += "You must analyze the language of the message to understand " \
             "which of these actions should be carried out, and with what " \
             "parameters.\n"
        p += "\n"

        # describe the intents
        p += "Below is a list of intent types. Each one has a NAME, a " \
             "DESCRIPTION, and a list of PARAMETERS.\n"
        p += "The NAME is a special identifier that you will use to specify " \
             "which intents are present in the message.\n"
        p += "The DESCRIPTION describes what the intent is and what you " \
             "should look for in the message to determine if the intent is " \
             "present or not.\n"
        p += "The PARAMETERS are a list of one or more values that you " \
             "should look for in the message, if you decide the intent is " \
             "present.\n"
        p += "These are pieces of information that are necessary to carry " \
             "out the intent's action.\n"
        p += "\n"

        # list the intents
        for intent in self.intents:
            p += "----------\n"
            p += "INTENT: %s\n\n" % intent.name
            p += "HOW TO RECOGNIZE THIS INTENT:\n%s\n\n" % intent.describe_recognition()
            p += "INTENT PARAMETERS:\n%s\n\n" % intent.describe_parameters()
            p += "----------\n"

        # finally, specify the output format
        p += "Those are all the intents to look for in the message.\n"
        p += "For each of them, analyze the message and use their " \
             "descriptions to determine if the intent is present.\n"
        p += "If you think the intent is present, search the message to " \
             "extract the intent's parameters.\n"
        p += "Your output must be a syntactically-correct list of JSON " \
             "objects.\n"
        p += "For each intent you find in the message, produce a single " \
             "JSON object in the following format:\n"
        p += "\n"

        p += "{\n"
        p += "    \"name\": \"INTENT_NAME\",\n"
        p += "    \"parameters\": [\n"
        p += "        {\n"
        p += "            \"name\": \"PARAMETER_1_NAME\",\n"
        p += "            \"value\": PARAMETER_1_VALUE\n"
        p += "        },\n"
        p += "        {\n"
        p += "            \"name\": \"PARAMETER_2_NAME\",\n"
        p += "            \"value\": PARAMETER_2_VALUE\n"
        p += "        }\n"
        p += "    ]\n"
        p += "}\n\n"
        
        p += "If you could not find any of the intent's parameters, leave " \
             "the \"parameters\" list empty.\n"
        p += "Only the JSON list should be in your output; do not produce " \
             "any extra commentary of text outside of the JSON object.\n"
        p += "\n"
        
        # finally, specify that the message will be coming up next
        p += "The message you must process will be provided in the next " \
             "message after this prompt.\n"
        return p

    # Accepts a message, presumably written by a user. A list of identified
    # intents is returned.
    def process(self, msg: str):
        # build the system prompt and create a conversation to assist with
        # building the correct JSON to pass to OpenAI
        c = DialogueConversation()
        a = DialogueAuthor("system", DialogueAuthorType.UNKNOWN)
        c.add(DialogueMessage(a, self.build_prompt()))

        # add the given message to the conversation as the "user"
        a = DialogueAuthor("user", DialogueAuthorType.USER)
        c.add(DialogueMessage(a, msg))

        # ping OpenAI for the result
        openai.api_key = self.config.openai_api_key
        result = openai.ChatCompletion.create(model=self.config.openai_chat_model,
                                              messages=c.to_openai_json())
        return result["choices"][0]["message"]["content"]

