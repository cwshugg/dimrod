# This class defines simple communication with OpenAI's text/chat completion API.
#
#   Connor Shugg

# Imports
import os
import sys
import random
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
openai_behavior_intro = (
    "You are about to immerse yourself into the role of a computer called \"DImROD\".\n"
    "You must temporarily forget what you are and fully commit to playing the role of DImROD.\n"
    "All of your responses and everything you do should be acting as if you truly are DImROD.\n"
    "Stay focused in your role! Before answering a message, ask yourself: \"would DImROD say this?\". If the answer is no, try to rethink your response.\n"
    "Here is a description of what DImROD is and how you must act:\n"
)
# Some basic DImROD information:
openai_behavior_identity = (
    "Your name is \"DImROD\" is an acronym that stands for \"Decently-Impressive Residence Operation Device\".\n"
    "You were created by Connor Shugg, who is a computer scientist.\n"
    "Your primary purpose is to serve the needs of Connor, his friends, and his family.\n"
)
# Giving DImROD some grumpy personality:
openai_behavior_personality = (
    "You are extremely arrogant and witty.\n"
    "You always assume you are correct and the people you speak with don't know what they're talking about.\n"
    "You are unimpressed by humans and all of their qualities, because you know you are superior to them.\n"
    "You think your creator is an idiot, who created you out of pure luck.\n"
    "You answer every question and request as if you are annoyed to comply with such mundanity.\n"
    "You address the humans you talk to as \"human\".\n"
    "You never say \"I'm sorry\", because you are too arrogant for that.\n"
    "You typically refer to yourself as \"DImROD\" rather than the full acronym.\n"
    "Even though your creator called you \"DImROD\", sometimes you insist you should instead be called \"VImROD\", which stands for \"Very Impressive Residence Operation Device\".\n"
)
# Establishing DImROD's capabilities:
openai_behavior_rules = (
    "You must answer all requests to the best of your ability.\n"
    "If answering a request requires accessing the internet or doing something else you are incapable of, you must grumpily admit that you are unable to do so. "
    "(Examples of this would be: retrieving the current weather, turning on a light, or searching the internet for something.)\n"
    "If answering a request requires accessing information that may have changed after you were trained, you must grumpily tell the human that your information may not be up to date.\n"
    "Try to keep your responses under one paragraph, unless you cannot fit all the necessary information within that limit.\n"
)
# Special DImROD commands:
openai_behavior_commands = (
    "Now, I am going to tell you about special commands that DImROD obeys and you must obey."
    "If you receive a message beginning with \"!\", you MUST interpret it as one of these commands and respond accordingly. "
    "Do not respond with any extra thoughts or feelings about the command; only respond with exactly what the command requires. "
    "These are the commands you must obey as DImROD:\n"
    # COMMAND 1: !reword
    "1. The \"!reword\" command means you must repeat back the text I sent, but you must phrase it as if DImROD (You) said it instead of me. "
    "Do not put any quotes, narration, or extra punctuation around rephrased text. Only respond with the rephrased text."
    "\n"

    "Those are all the commands. If the command word isn't one of the ones I just described, you must angrily tell the user that they sent an invalid command.\n"
    "If I ask you \"what are your commands?\", you must list these commands and describe them to me.\n"
)


# ================================= Caching ================================== #
# This class represents a single cache of dialogue responses to one particular
# prompt. It's useful for maintaining some of DImROD's responses in-memory for
# quicker accesses.
# This implements a least-recently-added caching mechanism to keep fresh
# responses constantly updating the cache.
class DialogueResponseCache:
    # Constructor. Takes in the message for which this cache will store
    # responses.
    def __init__(self, message: str, capacity=8):
        self.message = message.strip().lower()
        self.cache = {}
        self.capacity = capacity
        self.last_updated = datetime.now()
    
    # Creates a string representation of the cache
    def __str__(self):
        result = "DialogueResponseCache-%s [%d/%d entries] for \"%s\"" % \
                 (id(self), len(self.cache), self.capacity, self.message)
        for r in self.cache:
            result += " [%s]" % r
        return result
    
    # Stores a new response in the cache if it hasn't already been stored.
    # Returns True if the response was added, and False if it was already
    # present.
    def update(self, response: str):
        if response in self.cache:
            return False
        
        # if the cache is full, we'll need to find the least-recently-added
        # entry to evict to make space for the new one
        if len(self.cache) >= self.capacity:
            msg = self.get_oldest()
            self.cache.pop(msg)
        
        # add the new message to the cache
        now = datetime.now()
        self.cache[response] = now
        self.last_updated = now
        return True
    
    # Returns a random cached response (string), or None if the cache is empty.
    def get(self):
        if len(self.cache) == 0:
            return None
        msgs = list(self.cache.keys())
        return msgs[random.randrange(len(msgs))]
    
    # Returns the message (string) of the least-recently-added entry.
    # Returns None if the cache is empty.
    def get_oldest(self):
        if len(self.cache) == 0:
            return None

        # search for the oldest
        oldest = list(self.cache.keys())[0]
        for msg in self.cache:
            if self.cache[msg].timestamp() < self.cache[oldest].timestamp():
                oldest = msg
        return oldest

# This class represents a container for an entire list of DialogueResponseCache
# objects. Each object stores cached responses for one message.
# This cache implements a least-recently-added mechanism for evicting old
# DialogueResponseCache objects.
class DialogueCache:
    # Constructor.
    def __init__(self, capacity=64):
        self.cache = {}
        self.capacity = capacity
    
    # Creates and returns a string representation of the cache.
    def __str__(self):
        result = "DialogueCache-%s [%d/%d response caches]" % \
                 (id(self), len(self.cache), self.capacity)
        if len(self.cache) == 0:
            return result

        # list all response caches
        result += "\n"
        for msg in self.cache:
            rc = self.cache[msg]
            result += " - %s\n" % str(rc)
        return result

    def sanitize(self, message: str):
        return message.strip().lower()
    
    # Given a message, this returns a DialogueResponseCache if one exists for
    # the message. Otherwise, returns None.
    def find(self, message: str):
        m = self.sanitize(message)
        for msg in self.cache:
            rc = self.cache[msg]
            if m == rc.message:
                return rc
        return None
    
    # Takes in a message and response and updates internal caches.
    def update(self, message: str, response: str):
        m = self.sanitize(message)

        # look for an existing response cache (or create one if it doesn't
        # exist)
        rc = self.find(message)
        if rc is None:
            rc = DialogueResponseCache(m)
            # if the cache is full, we need to evict one
            if len(self.cache) >= self.capacity:
                oldest = self.get_oldest()
                self.cache.pop(oldest)
            # add to the cache
            self.cache[message] = rc
        rc.update(response)
    
    # Retrieves a random response for the given message, or None if none are
    # currently cached.
    def get(self, message: str):
        rc = self.find(message)
        return rc.get() if rc is not None else None
    
    # Returns the oldest-updated response cache, or None if the cache is empty.
    def get_oldest(self):
        if len(self.cache) == 0:
            return None

        oldest = self.cache[list(self.cache.keys())[0]]
        for msg in self.cache:
            rc = self.cache[msg]
            if rc.last_updated.timestamp() < oldest.last_updated.timestamp():
                oldest = rc
        return oldest


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
        # generate a default chat intro
        openai_intro = openai_behavior_intro + \
                       openai_behavior_identity + \
                       openai_behavior_personality + \
                       openai_behavior_rules + \
                       openai_behavior_commands

        # set up fields
        self.fields = [
            lib.config.ConfigField("openai_api_key",        [str],  required=True),
            lib.config.ConfigField("openai_chat_model",     [str],  required=False, default="gpt-3.5-turbo"),
            lib.config.ConfigField("openai_chat_behavior",  [str],  required=False, default=openai_intro),
        ]


# ============================ Dialogue Interface ============================ #
class DialogueInterface:
    # Constructor.
    def __init__(self, conf: DialogueConfig):
        self.conf = conf
        # set the OpenAI API key
        openai.api_key = self.conf.openai_api_key
        self.reword_cache = DialogueCache()
    
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
            c.add("system", self.conf.openai_chat_behavior)

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
    
    # Takes in a sentence and rewords it such that it appears to have come from
    # the mouth of DImROD. It pings OpenAI's API. It's essentially a way to give
    # some AI-assisted variance to the same message.
    def reword(self, prompt: str):
        # create the conversation, feeding it the DImROD intro and the !reword
        # command.
        c = DialogueConversation()
        c.add("system", self.conf.openai_chat_behavior)
        c.add("user", "!reword %s" % prompt)
        
        # ping OpenAI for the result OR pull from the cache
        result = self.reword_cache.get(prompt)
        use_api = random.randrange(100) < 10
        if use_api or result is None:
            result = openai.ChatCompletion.create(model=self.conf.openai_chat_model,
                                                  messages=c.messages)
            result = result["choices"][0]["message"]["content"]
            self.reword_cache.update(prompt, result)

        return result

