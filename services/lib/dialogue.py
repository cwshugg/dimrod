# This class defines simple communication with OpenAI's text/chat completion API.
#
#   Connor Shugg

# Imports
import os
import sys
import random
import hashlib
from datetime import datetime
from enum import Enum
import openai
import sqlite3

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
import lib.config
import lib.pack

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
    "Now, I am going to tell you about special commands that DImROD obeys and you must obey.\n"
    "If you receive a message beginning with \"!\", you MUST interpret it as one of these commands and respond accordingly. "
    "Do not respond with any extra thoughts or feelings about the command; only respond with exactly what the command requires. "
    "These are the commands you must obey as DImROD:\n"
    # COMMAND 1: !reword
    "1. The \"!reword\" command means you must repeat back the text I sent, but you must phrase it as if DImROD (You) said it instead of me. "
    "Do not put any quotes, narration, or extra punctuation around rephrased text. Only respond with the rephrased text."
    "\n"

    "Those are all the commands. If the command word isn't one of the ones I just described, you must tell the user that they sent an invalid command.\n"
    "If I ask you \"what are your commands?\", you must list these commands and describe them to me.\n"
)


# =============================== Conversation =============================== #
# This enum represents the various types of speakers in dialogue.
class DialogueAuthorType(Enum):
    UNKNOWN = -1
    # DImROD author types
    SYSTEM = 0
    SYSTEM_TELEGRAM = 1
    # User author types
    USER = 1000
    USER_TELEGRAM = 1001

# This class represents a single speaker in a dialogue (ex: DImROD itself, a
# telegram user, etc.)
class DialogueAuthor:
    # Constructor.
    def __init__(self, name: str, atype: DialogueAuthorType, aid=None):
        self.name = name
        self.atype = atype
        self.aid = aid
        if self.aid is None:
            self.get_id()

    # Returns a string representation of the object.
    def __str__(self):
        return "DialogueAuthor: [%d-%s] %s" % \
               (self.atype.value, self.atype.name, self.name)
    
    # Returns the author's unique ID. If one hasn't been created yet for this
    # instance, one is generated here.
    def get_id(self):
        if self.aid is None:
            data = "%s-%s" % (self.name, self.atype.name)
            data = data.encode("utf-8")
            self.aid = hashlib.sha256(data).hexdigest()
        return self.aid
    
    # Returns, based on the author's type, if it's a system author.
    def is_system(self):
        return self.atype.value >= DialogueAuthorType.SYSTEM.value and \
               self.atype.value < DialogueAuthorType.USER.value
    
    # Returns, based on the author's type, if it's a user author.
    def is_user(self):
        return self.atype.value >= DialogueAuthorType.USER.value

    # ------------------------------- SQLite3 -------------------------------- #
    # Creates and returns an SQLite3-friendly tuple version of the object.
    def to_sqlite3(self):
        result = (self.get_id(), self.atype.value, self.name)
        return result
    
    # Takes in a SQLite3 tuple and creates a DialogueAuthor object.
    @staticmethod
    def from_sqlite3(tdata: tuple):
        assert len(tdata) >= 3
        atype = DialogueAuthorType(tdata[1])
        return DialogueAuthor(tdata[2], atype, aid=tdata[0])

# This class represents a single message passed between a user and DImROD.
class DialogueMessage:
    # Constructor.
    def __init__(self, author: DialogueAuthor, content: str,
                 mid=None, timestamp=datetime.now()):
        self.author = author
        self.content = content
        self.timestamp = timestamp
        self.mid = mid
        if self.mid is None:
            self.get_id()
    
    # Returns the message ID. If one hasn't been created yet for this instance,
    # one is generated here.
    def get_id(self):
        if self.mid is None:
            # combine the author, content, and timestamp into a collection of
            # bytes (with a few extra bytes thrown in for good measure), then
            # use it to generate a unique hash
            data = "%s-%s-%d" % (self.author.get_id(), self.content, self.timestamp.timestamp())
            data = data.encode("utf-8") + os.urandom(8)
            self.mid = hashlib.sha256(data).hexdigest()
        return self.mid
    
    # Converts the message into a JSON dictionary formatted for the OpenAI API.
    def to_openai_json(self):
        return {"role": self.author.name, "content": self.content}

    # ------------------------------- SQLite3 -------------------------------- #
    # Converts the object into a SQLite3-friendly tuple.
    def to_sqlite3(self):
        result = (self.get_id(), self.author.get_id(), self.content, self.timestamp.timestamp())
        return
    
    # Converts the given SQlite3 tuple into a DialogueMessage object.
    @staticmethod
    def from_sqlite3(tdata: tuple):
        assert len(tdata) >= 4
        ts = datetime.fromtimestamp(tdata[3])
        author = tdata[1] # FIXME NEED TO TURN AUTHOR ID INTO AUTHOR OBJECT REFERENCE
        m = DialogueMessage(tdata[1], tdata[2], mid=tdata[0], timestamp=ts)
        return m

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
    def add(self, msg: DialogueMessage):
        self.messages.append(msg)
    
    # Returns the latest user request (role = "user"), or None.
    def latest_request(self):
        for m in list(reversed(self.messages)):
            if m.author.is_user():
                return m
        return None

    # Returns the latest DImROD answer, or None.
    def latest_response(self):
        for m in list(reversed(self.messages)):
            if m.author.is_system():
                return m
        return None
    
    # Converts the conversation's messages to a JSON dictionary suitable for
    # OpenAI's API.
    def to_openai_json(self):
        result = []
        for m in self.messages:
            result.append(m.to_openai_json())
        return result


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

        # set up default database locations
        default_db_dir = os.path.dirname(__file__)
        default_author_db_path = os.path.join(default_db_dir, ".dialogue_authors.db")
        default_convo_db_path = os.path.join(default_db_dir, ".dialogue_conversations.db")

        # set up fields
        self.fields = [
            lib.config.ConfigField("openai_api_key",            [str],  required=True),
            lib.config.ConfigField("openai_chat_model",         [str],  required=False, default="gpt-3.5-turbo"),
            lib.config.ConfigField("openai_chat_behavior",      [str],  required=False, default=openai_intro),
            lib.config.ConfigField("dialogue_author_db",        [str],  required=False, default=default_author_db_path),
            lib.config.ConfigField("dialogue_conversation_db",  [str],  required=False, default=default_convo_db_path)
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
            a = DialogueAuthor("system", DialogueAuthorType.UNKNOWN)
            self.save_author(a)
            m = DialogueMessage(a, self.conf.openai_chat_behavior)
            c.add(m)

        # add the user's message to the conversation and contact OpenAI
        a = DialogueAuthor("user", DialogueAuthorType.USER)
        self.save_author(a)
        m = DialogueMessage(a, prompt)
        c.add(m)
        result = openai.ChatCompletion.create(model=self.conf.openai_chat_model,
                                              messages=c.to_openai_json())

        # grab the first response choice and add it to the conversation
        choices = result["choices"]
        response = choices[0]
        a = DialogueAuthor("assistant", DialogueAuthorType.SYSTEM)
        self.save_author(a)
        m = DialogueMessage(a, response["message"]["content"])
        c.add(m)
        return c
    
    # Takes in a sentence and rewords it such that it appears to have come from
    # the mouth of DImROD. It pings OpenAI's API. It's essentially a way to give
    # some AI-assisted variance to the same message.
    def reword(self, prompt: str):
        # create the conversation, feeding it the DImROD intro and the !reword
        # command.
        c = DialogueConversation()
        a = DialogueAuthor("system", DialogueAuthorType.UNKNOWN)
        c.add(DialogueMessage(a, self.conf.openai_chat_behavior))
        a = DialogueAuthor("user", DialogueAuthorType.USER)
        c.add(DialogueMessage("user", "!reword %s" % prompt))
        
        # ping OpenAI for the result
        result = openai.ChatCompletion.create(model=self.conf.openai_chat_model,
                                              messages=c.to_openai_json())
        result = result["choices"][0]["message"]["content"]
        return result

    # -------------------------- SQLite3 Databasing -------------------------- #
    # Saves an author to the author database.
    def save_author(self, author: DialogueAuthor, db_path=None):
        db_path = self.conf.dialogue_author_db if db_path is None else db_path

        # connect and make sure the table exists
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS authors (\n"
                    "aid TEXT PRIMARY KEY, "
                    "atype INTEGER, "
                    "name TEXT)")

        # insert the author into the database
        cur.execute("INSERT OR REPLACE INTO authors VALUES %s" %
                    str(author.to_sqlite3()))
        con.commit()
    
    # Searches for authors in the database based on one or more authors fields.
    # (If NO fields are specified, all stored authors are returned.)
    # Returns an empty list or a list of matching DialogueAuthor objects.
    def search_author(self, aid=None, name=None, atype=None, db_path=None):
        db_path = self.conf.dialogue_author_db if db_path is None else db_path

        # if the database doesn't exist, don't bother
        if not os.path.isfile(db_path):
            return []

        # otherwise, connect to the database
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        # build a command
        cmd = "SELECT * FROM authors"
        conditions = []
        if aid is not None:
            conditions.append("aid == \"%s\"" % aid)
        if name is not None:
            conditions.append("name == \"%s\"" % name)
        if atype is not None:
            conditions.append("atype == %d" % atype)
        if len(conditions) > 0:
            cmd += " WHERE "
        for (i, c) in enumerate(conditions):
            if i == 0:
                cmd += " WHERE "
            cmd += c
            if i < len(conditions) - 1:
                cmd += " AND "

        # execute the search and build an array of authors
        result = []
        for row in cur.execute(cmd):
            author = DialogueAuthor.from_sqlite3(row)
            result.append(author)
        con.close()
        return result

