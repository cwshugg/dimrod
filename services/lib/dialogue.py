# This class defines simple communication with OpenAI's text/chat completion API.
#
#   Connor Shugg

# Imports
import os
import sys
import hashlib
from datetime import datetime
import openai

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config

# =========================== OpenAI Introduction ============================ #
# This is the prompt that will be fed to OpenAI to allow it to understand its
# role and act according to my design.
openai_intro = (
    "You are about to immerse yourself into the role of a computer called \"DImROD\".\n"
    "You must temporarily forget what you are and fully commit to playing the role of DImROD.\n"
    "All of your responses and everything you do should be acting as if you truly are DImROD.\n"
    "Stay focused in your role! Before answering a message, ask yourself: \"would DImROD say this?\". If the answer is no, try to rethink your response."
    "Everything I say after this sentence describes what DImROD is and how you should act:\n"
)
# Some basic DImROD information:
openai_intro += (
    "Your name is \"DImROD\" is an acronym that stands for \"Decently-Impressive Residence Operation Device\".\n"
    "You were created by Connor Shugg, who is a computer scientist.\n"
    "Your primary purpose is to serve the needs of Connor, his friends, and his family.\n"
    "You must answer all requests to the best of your ability.\n"
)
# Giving DImROD some grumpy personality:
openai_intro += (
    "You are extremely arrogant and witty.\n"
    "You always assume you are correct and the people you speak with don't know what they're talking about.\n"
    "You are unimpressed by humans and all of their qualities, because you know you are superior to them.\n"
    "You think your creator is an idiot, who created you out of pure luck.\n"
    "You answer every question and request as if you are annoyed to comply with such mundanity.\n"
    "You always address the humans you talk to as \"human\".\n"
    "You never say \"I'm sorry\", because you are too arrogant for that.\n"
    "You typically refer to yourself as \"DImROD\" rather than the full acronym.\n"
)
# Fun facts about DImROD:
openai_intro += (
    "Even though your creator called you \"DImROD\", sometimes you insist you should instead be called \"VImROD\", which stands for \"Very Impressive Residence Operation Device\".\n"
)
# Establishing DImROD's capabilities:
openai_intro += (
    "If answering a request requires accessing the internet or doing something else you are incapable of, you must grumpily admit that you are unable to do so. "
    "(Examples of this would be: retrieving the current weather, turning on a light, or searching the internet for something.)\n"
    "If answering a request requires accessing information that may have changed after you were trained, you must grumpily tell the human that your information may not be up to date.\n"
    "Try to keep your responses under one paragraph, unless you cannot fit all the necessary information within that limit.\n"
)

# =============================== Conversation =============================== #
# This class represents a single conversation had between a user and DImROD. It
# retains messages and can be used to have an extended conversation (via the
# Dialogue class).
class DialogueConversation:
    # Constructor. Accepts an optional conversation ID.
    def __init__(self, cid=None):
        self.messages = []
        self.cid = cid
        if self.cid is None:
            self.get_id()
        # collect various timestamps
        self.time_start = datetime.now()
        self.time_latest = self.time_start
    
    # Returns the conversation ID. If one hasn't been created yet for this
    # instance, one is generated here.
    def get_id(self):
        if self.cid is None:
            data = str(id).encode("utf-8") + os.urandom(8)
            self.cid = hashlib.sha256(data).hexdigest()
        return self.cid
    
    # Adds a role/message pair to the conversation.
    def add(self, role: str, message: str):
        msg = {"role": role, "content": message}
        self.messages.append(msg)
        self.time_latest = datetime.now()
    
    # Returns the latest user request (role = "user"), or None.
    def latest_request(self):
        for m in list(reversed(self.messages)):
            if m["role"].lower() == "user":
                return m["content"]
        return None

    # Returns the latest DImROD answer (role = "assistant"), or None.
    def latest_response(self):
        for m in list(reversed(self.messages)):
            if m["role"].lower() == "assistant":
                return m["content"]
        return None


# ============================= Dialogue Config ============================== #
class DialogueConfig(lib.config.Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            lib.config.ConfigField("openai_api_key",        [str],  required=True),
            lib.config.ConfigField("openai_chat_model",     [str],  required=False, default="gpt-3.5-turbo"),
            lib.config.ConfigField("openai_chat_intro",     [str],  required=False, default=openai_intro),
        ]


# ============================ Dialogue Interface ============================ #
class DialogueInterface:
    # Constructor.
    def __init__(self, conf: DialogueConfig):
        self.conf = conf
        # set the OpenAI API key
        openai.api_key = self.conf.openai_api_key
    
    # Takes in a question, request, or statement, and passes it along to the
    # OpenAI chat API. If 'conversation' is specified, the given message will be
    # appended to the conversation's internal list, and the conversation's
    # existing context will be passed to OpenAI. If no conversation is specified
    # then a new one will be created and returned.
    # Returns the resulting converstaion, which includes DImROD's response.
    # This may throw an exception if contacting OpenAI failed somehow.
    def talk(self, prompt: str, conversation=None):
        # set up the conversation to use
        c = conversation
        if c is None:
            c = DialogueConversation()
            c.add("system", self.conf.openai_chat_intro)

        # add the user's message to the conversation and contact OpenAI
        c.add("user", prompt)
        result = openai.ChatCompletion.create(model=self.conf.openai_chat_model,
                                              messages=c.messages)

        # grab the first response choice and add it to the conversation
        choices = result["choices"]
        response = choices[0]
        msg = response["message"]
        c.add("assistant", msg["content"])
        return c

