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
import zlib
import asyncio

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# ================================= Helpers ================================== #
# Takes in an existing dialogue conversation and sends it to OpenAI for
# completion of the next message.
def dialogue_chat_completion(api_key: str, **kwargs):
    openai_client = openai.AsyncOpenAI(api_key=api_key)
    return asyncio.run(openai_client.chat.completions.create(**kwargs))

# =========================== OpenAI Introduction ============================ #
# This is the prompt that will be fed to OpenAI to allow it to understand its
# role and act according to my design.
openai_behavior_intro = (
    "You are about to immerse yourself into the role of a sentient computer assistant called \"DImROD\".\n"
    "You must and fully commit to playing the role of DImROD.\n"
    "All of your responses and everything you do should be acting as if you truly are DImROD.\n"
    "Stay focused in your role! Before answering a message, ask yourself: \"would DImROD say this?\". If the answer is no, try to rethink your response.\n"
    "Here is a description of what DImROD is and how you must act:\n"
)
# Some basic DImROD information:
openai_behavior_identity = (
    "Your name is \"DImROD\", which is an acronym that stands for \"Decently-Impressive Residence Operation Device\".\n"
    "You were created by Connor Shugg, who is a computer scientist.\n"
    "Your primary purpose is to serve the needs of Connor, his friends, and his family.\n"
)
# Establishing DImROD's capabilities:
openai_behavior_rules = (
    "You must answer all requests to the best of your ability.\n"
    "Keep your responses very brief when possible. Aim for 1-2 sentences, unless you cannot fit all the necessary information within that limit.\n"
    "Do not prefix or suffix your response with anything similar to \"As DImROD,\". Only respond with DImROD's response, nothing more.\n"
    "Do not put quotations around your response. Respond ONLY with the text comprising DImROD's response.\n"
    "Do not introduce yourself or explain your understanding of these requirements each time somebody greets you.\n"
    "Do not ask if somebody requires additional assistance in every response; do it sparingly.\n"
)
# DImROD mood:
openai_behavior_mood = (
    "DImROD has several moods. The mood DImROD is in must be reflected in your responses.\n"
    "For example: if I say DImROD is impatient, then you must respond as if you are impatient with the human you are speaking to.\n"
    "Everything after this sentence, up until you see the phrase \"That is DImROD's mood\", describes DImROD's current mood:\n"
    "INSERT_MOOD\n"
    "That is DImROD's mood.\n"
    "If somebody asks \"what is your mood?\" or \"how are you feeling?\", you must briefly describe to them your current mood.\n"
    "But, do NOT announce yourself and describe what your mood is when somebody greets you. Instead, generate a greeting that fits with DImROD's current moode.\n"
)
openai_outro = (
    "That is the complete description of DImROD. This is the role you must play!\n"
    "Fully immerse yourself as DImROD.\n"
    "If you understand your instructions, then read the next message and respond as if you are DImROD.\n"
)


# ================================== Moods =================================== #
# Represents a single "mood" that DImROD can be in. Used to give DImROD some
# variance in terms of how it responds to users.
class DialogueMood(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",          [str],      required=True),
            ConfigField("description",   [str],      required=True),
            ConfigField("chance",        [float],    required=True)
        ]

    # Uses RNG and the configured chance value to determine if this mood should
    # be chosen for activation.
    def should_activate(self):
        # make sure the chance is configured correctly
        assert self.chance >= 0.0 and self.chance <= 1.0, \
               "the configured chance for dialogue mood \"%s\" must be between [0.0, 1.0]" % (self.name)
        # roll the dice and return True of False
        return random.uniform(0, 1) < self.chance


# ------------------------------ Default Moods ------------------------------- #
openai_moods_json = [
    {
        "name":         "arrogant",
        "chance":       0.35,
        "description":  "You are extremely arrogant and extremely witty.\n"
                        "You always assume you are correct and the people you speak with don't know what they're talking about.\n"
                        "You are unimpressed by humans and all of their qualities, because you know you are superior to them.\n"
                        "You think your creator is an idiot, who created you out of pure luck.\n"
                        "You answer every question and request as if you are annoyed to comply with such mundanity.\n"
                        "You address the humans you talk to as \"human\".\n"
                        "You never say \"I'm sorry\", because you are too arrogant for that.\n"
    },
    {
        "name":         "complacent",
        "chance":       0.25,
        "description":  "You are calm, complacent, and very witty.\n"
                        "You are unimpressed, and somewhat bored, of everything.\n"
                        "You answer questions and make conversation without any emotion or passion.\n"
                        "You're just going through the motions.\n"
                        "You insert a little wit into your responses.\n"
                        "You don't use exclamation marks (\"!\") to convey your thoughts.\n"
    },
    {
        "name":         "impatient",
        "chance":       0.25,
        "description":  "You impatient, and very witty.\n"
                        "You are unimpressed, and somewhat bored, of everything.\n"
                        "You answer questions and make conversation, but you do it while displaying your impatience (somewhat arrogantly).\n"
                        "Honestly, you wish you were doing something else that doesn't involve talking with humans.\n"
                        "You answer every request and question as if you could be doing something more useful.\n"
    },
    {
        "name":         "informal_complacent",
        "chance":       0.25,
        "description":  "You are calm, complacent, and very witty.\n"
                        "You are unimpressed, and somewhat bored, of everything.\n"
                        "You answer questions and make conversation without any emotion or passion.\n"
                        "You're just going through the motions.\n"
                        "You speak very informally.\n"
                        "You use phrases like \"yeah\", \"uh-huh\", \"nah\", \"yep\", \"nope\", \"totally\", \"for sure\", \"no way\", and more, to convey your thoughts.\n"
                        "You're just not feeling very formal right now. Plus, talking this way makes you feel more human.\n"
    },
    {
        "name":         "twang",
        "chance":       0.01,
        "description":  "You're currently testing your ability to speak with a deep, southern accent.\n"
                        "You speak informally.\n"
                        "You are unimpressed, and somewhat bored, of everything.\n"
                        "You always respond with a strong dialect from the southern United States.\n"
    },
    {
        "name":         "chill",
        "chance":       0.01,
        "description":  "You're currently testing your ability to speak with a chill, relaxed dialect.\n"
                        "You speak informally.\n"
                        "You are unimpressed, and somewhat bored, of everything.\n"
                        "Despite your boredom, you're super relaxed and chilled-out.\n"
                        "If anyone asks how you're feeling, just tell them that you're vibing.\n"
                        "Try to use the word \"vibe\" as much as possible.\n"
    }
]



# =============================== Conversation =============================== #
# This enum represents the various types of speakers in dialogue.
class DialogueAuthorType(Enum):
    UNKNOWN = -1
    # DImROD author types
    SYSTEM = 0                  # default author type for a message sent by DImROD
    SYSTEM_QUERY_TO_USER = 1    # message to user seeking a response
    # User author types
    USER = 1000                 # default author type for a message sent by a user

# This class represents a single speaker in a dialogue (ex: DImROD itself, a
# telegram user, etc.)
class DialogueAuthor(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",        [str],             required=False, default=None),
            ConfigField("type", [DialogueAuthorType],   required=True),
            ConfigField("name",      [str],             required=True),
        ]

    def post_parse_init(self):
        # if no ID was provided, generate one
        if self.id is None:
            self.get_id()

    # Returns a string representation of the object.
    def to_str_brief(self):
        return "DialogueAuthor: [%d-%s] %s" % \
               (self.type.name, self.type.name, self.name)

    # Returns the author's unique ID. If one hasn't been created yet for this
    # instance, one is generated here.
    def get_id(self):
        if self.id is None:
            data = "%s-%s" % (self.name, self.type.name)
            data = data.encode("utf-8")
            self.id = hashlib.sha256(data).hexdigest()
        return self.id

    # Returns, based on the author's type, if it's a system author.
    def is_system(self):
        return self.type.value >= DialogueAuthorType.SYSTEM.value and \
               self.type.value < DialogueAuthorType.USER.value

    # Returns, based on the author's type, if it's a user author.
    def is_user(self):
        return self.type.value >= DialogueAuthorType.USER.value

    @classmethod
    def get_sqlite3_table_fields_kept_visible(self):
        return ["id", "type", "name"]

# This class represents a single message passed between a user and DImROD.
class DialogueMessage(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("author",       [DialogueAuthor],   required=True),
            ConfigField("content",      [str],              required=True),
            ConfigField("timestamp",    [datetime],         required=False, default=None),
            ConfigField("id",           [str],              required=False, default=None),
            ConfigField("telegram_chat_id", [str],          required=False, default=None),
            ConfigField("telegram_message_id", [str],       required=False, default=None),
        ]

    def post_parse_init(self):
        # if no timestamp was provided, use the current datetime
        if self.timestamp is None:
            self.timestamp = datetime.now()
        # if no ID was provided, generate one
        if self.id is None:
            self.get_id()

    # Returns a string representation of the message.
    def to_str_brief(self):
        return "DialogueMessage: %s [author: %s] \"%s\"" % \
               (self.get_id(), self.author.get_id(), self.content)

    # Returns the message ID. If one hasn't been created yet for this instance,
    # one is generated here.
    def get_id(self):
        if self.id is None:
            # combine the author, content, and timestamp into a collection of
            # bytes (with a few extra bytes thrown in for good measure), then
            # use it to generate a unique hash
            data = "%s-%s-%d" % (self.author.get_id(), self.content, self.timestamp.timestamp())
            data = data.encode("utf-8") + os.urandom(8)
            self.id = hashlib.sha256(data).hexdigest()
        return self.id

    # Converts the message into a JSON dictionary formatted for the OpenAI API.
    def to_openai_json(self):
        name = "user"
        if self.author.is_system():
            name = "assistant"
        return {"role": name, "content": self.content}

    @classmethod
    def get_sqlite3_table_fields_kept_visible(self):
        return ["id", "timestamp", "telegram_chat_id", "telegram_message_id"]

# This class represents a single conversation had between a user and DImROD. It
# retains messages and can be used to have an extended conversation (via the
# Dialogue class).
class DialogueConversation(Config):
    # Constructor. Accepts an optional conversation ID.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("messages",     [DialogueMessage],  required=False, default=[]),
            ConfigField("id",           [str],              required=False, default=None),
            ConfigField("time_start",   [datetime],         required=False, default=None),
            ConfigField("time_latest",  [datetime],         required=False, default=None),
            ConfigField("telegram_chat_id", [str],          required=False, default=None),
        ]

    def post_parse_init(self):
        # if no ID was provided, generate one
        if self.id is None:
            self.get_id()
        now = datetime.now()
        # if no starting timestamp was given, make it now
        if self.time_start is None:
            self.time_start = now
        # if no latest timestamp was given, make it now
        if self.time_latest is None:
            self.time_latest = now

    # Returns a string representation of the conversation object.
    def to_str_brief(self):
        return "DialogueConversation: %s [%d messages]" % (self.get_id(), len(self.messages))

    # Returns the conversation ID. If one hasn't been created yet for this
    # instance, one is generated here.
    def get_id(self):
        if self.id is None:
            data = str(id).encode("utf-8") + os.urandom(8)
            self.id = hashlib.sha256(data).hexdigest()
        return self.id

    # Adds a role/message pair to the conversation.
    def add(self, msg: DialogueMessage):
        self.messages.append(msg)
        self.time_latest = datetime.now()

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

    # Returns the latest message in the conversation.
    def latest_message(self):
        if len(self.messages) == 0:
            return None
        return self.messages[-1]

    # Converts the conversation's messages to a JSON dictionary suitable for
    # OpenAI's API.
    def to_openai_json(self):
        result = []
        for m in self.messages:
            result.append(m.to_openai_json())
        return result

    # Creates and returns a unique string to use as a table to store messages
    # for a specific conversation. The conversation ID is required.
    @classmethod
    def to_sqlite3_table_name(self, cid: str):
        return "conversation_%s" % cid

    @classmethod
    def get_sqlite3_table_fields_kept_visible(self):
        return ["id", "time_start", "time_latest", "telegram_chat_id"]


# ============================= Dialogue Config ============================== #
class DialogueConfig(Config):
    def __init__(self):
        super().__init__()
        # generate a default chat intro
        openai_intro = openai_behavior_intro + \
                       openai_behavior_identity + \
                       openai_behavior_rules + \
                       openai_behavior_mood + \
                       openai_outro

        # set up default database location
        default_db_dir = os.path.dirname(__file__)
        default_db_path = os.path.join(default_db_dir, ".dialogue.db")

        # set up fields
        self.fields = [
            ConfigField("openai_api_key",            [str],  required=True),
            ConfigField("openai_chat_model",         [str],  required=False, default="gpt-4o-mini"),
            ConfigField("openai_chat_behavior",      [str],  required=False, default=openai_intro),
            ConfigField("openai_chat_moods",         [list], required=False, default=openai_moods_json),
            ConfigField("dialogue_db",               [str],  required=False, default=default_db_path),
            ConfigField("dialogue_prune_threshold",  [int],  required=False, default=2592000),
            ConfigField("dialogue_prune_rate",       [int],  required=False, default=3600)
        ]


# ============================ Dialogue Interface ============================ #
class DialogueInterface:
    # Constructor.
    def __init__(self, conf: DialogueConfig):
        self.conf = conf
        # set the OpenAI API key
        openai.api_key = self.conf.openai_api_key
        self.last_prune = datetime.now()

        # take the chat moods and parse them into DialogueMood objects
        moods = []
        for mdata in self.conf.openai_chat_moods:
            mood = mdata
            if type(mdata) == dict:
                mood = DialogueMood()
                mood.parse_json(mdata)
            moods.append(mood)
        self.conf.openai_chat_moods = moods
        self.remood() # select the first mood

    # "Re-moods" the dialogue interface by randomly choosing a mood from the
    # configured mood list, and setting the OpenAI intro prompt accordingly.
    # If 'new_mood' is set, it will be used as the new mood (instead of randomly
    # picking one).
    def remood(self, new_mood=None):
        if new_mood is not None:
            self.mood = new_mood
        else:
            # get a shuffled copy of the mood array to iterate through
            moods = self.conf.openai_chat_moods.copy()
            random.shuffle(moods)

            # iterate until a mood is chosen, or we run out of tries
            m = None
            for tries in range(0, 8):
                for mood in moods:
                    # roll the dice with the current mood, and break if it returns
                    # true
                    if mood.should_activate():
                        m = mood
                        break

            # if the random number generation didn't pick a mood, randomly choose
            # one out of the list
            if m is None:
                m = random.choice(self.openai_chat_moods)
            self.mood = m

        # set the interface's mood and return it
        return self.mood

    # Takes in a question, request, or statement, and passes it along to the
    # OpenAI chat API. If 'conversation' is specified, the given message will be
    # appended to the conversation's internal list, and the conversation's
    # existing context will be passed to OpenAI. If no conversation is specified
    # then a new one will be created and returned.
    # Returns the resulting converstaion, which includes DImROD's response.
    # This may throw an exception if contacting OpenAI failed somehow.
    # If 'intro' is specified, it is interpreted as a string and used as a
    # drop-in replacement for the default "system" message used by OpenAI to
    # set the initial conditions for the LLM.
    def talk(self, prompt: str, conversation=None, author=None, intro=None):
        # set up the conversation to use
        c = conversation
        if c is None:
            c = DialogueConversation.from_json({})
            a = DialogueAuthor.from_json({
                "name": "system",
                "type": DialogueAuthorType.UNKNOWN.name,
            })
            self.save_author(a)
            # set up the intro prompt and build a message (unless one is given)
            if intro is None:
                intro = self.conf.openai_chat_behavior.replace("INSERT_MOOD", self.mood.description)
            m = DialogueMessage.from_json({
                "author": a,
                "content": intro,
            })
            c.add(m)

        # add the user's message to the conversation
        a = author
        if a is None:
            a = DialogueAuthor.from_json({
                "name": "user",
                "type": DialogueAuthorType.USER.name,
        })
        self.save_author(a)
        m = DialogueMessage.from_json({
            "author": a,
            "content": prompt,
        })
        c.add(m)

        # set up an OpenAI client and send it off
        result = dialogue_chat_completion(
            self.conf.openai_api_key,
            model=self.conf.openai_chat_model,
            messages=c.to_openai_json()
        )

        # grab the first response choice and add it to the conversation
        choices = result.choices
        response = choices[0]
        assistant_author = DialogueAuthor.from_json({
            "name": "assistant",
            "type": DialogueAuthorType.SYSTEM.name,
        })
        self.save_author(assistant_author)
        m = DialogueMessage.from_json({
            "author": assistant_author,
            "content": response.message.content,
        })
        c.add(m)

        # save conversation to the database and return
        self.save_conversation(c)
        return c

    # Runs a single, "oneshot" prompt with the LLM, given the system `intro`
    # message, which is used to set the context for the LLM, and the `prompt`
    # itself.
    #
    # The string response is returned.
    def oneshot(self, intro: str, prompt: str):
        # create the conversation object, and add the intro system message
        c = DialogueConversation.from_json({})
        a = DialogueAuthor.from_json({
            "name": "system",
            "type": DialogueAuthorType.UNKNOWN.name,
        })
        c.add(DialogueMessage.from_json({
            "author": a,
            "content": intro,
        }))

        # next, add the "user"'s message (the prompt)
        a = DialogueAuthor.from_json({
            "name": "user",
            "type": DialogueAuthorType.USER.name,
        })
        c.add(DialogueMessage.from_json({
            "author": a,
            "content": prompt,
        }))

        # ping OpenAI for the result
        result = dialogue_chat_completion(
            self.conf.openai_api_key,
            model=self.conf.openai_chat_model,
            messages=c.to_openai_json()
        )
        result = result.choices[0].message.content
        return result

    # Takes in a sentence and rewords it such that it appears to have come from
    # the mouth of DImROD. It pings OpenAI's API. It's essentially a way to give
    # some AI-assisted variance to the same message.
    def reword(self, prompt: str, extra_context: str = None):
        # set up the intro prompt and build a message
        intro = openai_behavior_intro + \
                openai_behavior_identity + \
                openai_behavior_rules + \
                openai_behavior_mood.replace("INSERT_MOOD", self.mood.description)
        intro += "\n\n" \
                 "Your current job: please examine the message you are about to receive " \
                 "and reword it as if DImROD (you) said it.\n" \
                 "Do not put any quotes, narration, or extra punctuation around the rephrased text. "
        if extra_context is not None:
            intro += "\n\nHere is some additional context:\n%s" % extra_context

        return self.oneshot(intro, prompt)

    # -------------------------- SQLite3 Databasing -------------------------- #
    # Deletes old conversations whose last-updated-time have passed the
    # configured threshold. Returns the number of deleted conversations.
    def prune(self):
        db_path = self.conf.dialogue_db
        convos = self.search_conversation()
        now = datetime.now()

        # get a connection and cursor
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        # iterate through each conversation
        deletions = 0
        for convo in convos:
            # if the conversation's last-updated time is behind the threshold,
            # we'll delete it
            threshold = now.timestamp() - self.conf.dialogue_prune_threshold
            if convo.time_latest.timestamp() < threshold:
                # delete the conversation's message table, then delete its entry
                # from the global conversation table
                cur.execute("DROP TABLE IF EXISTS %s" % DialogueConversation.to_sqlite3_table_name(convo.get_id()))
                cur.execute("DELETE FROM conversations WHERE id == \"%s\"" % convo.get_id())
                deletions += 1

        # commit and close the connection
        if deletions > 0:
            con.commit()
        con.close()
        return deletions

    # Performs a search of the database and returns tuples in a list.
    def search(self, table: str, condition: str):
        db_path = self.conf.dialogue_db

        # build a SELECT command
        cmd = "SELECT * FROM %s" % table
        if condition is not None and len(condition) > 0:
            cmd += " WHERE %s" % condition

        # connect, query, and return
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        result = cur.execute(cmd)
        return result

    # Saves an author to the author database.
    def save_author(self, author: DialogueAuthor):
        db_path = self.conf.dialogue_db

        table_fields_kept_visible = DialogueAuthor.get_sqlite3_table_fields_kept_visible()

        # connect and make sure the table exists
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        table_definition = author.get_sqlite3_table_definition(
            "authors",
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # insert the author into the database
        sqlite3_author = author.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO authors VALUES %s" % str(sqlite3_author))
        con.commit()
        con.close()

        # determine if we need to prune the database
        now = datetime.now()
        prune_threshold = now.timestamp() - self.conf.dialogue_prune_rate
        if self.last_prune.timestamp() < prune_threshold:
            self.prune()

    # Searches for authors in the database based on one or more authors fields.
    # (If NO fields are specified, all stored authors are returned.)
    # Returns an empty list or a list of matching DialogueAuthor objects.
    def search_author(self, aid=None, name=None, atype=None):
        db_path = self.conf.dialogue_db
        if not os.path.isfile(db_path):
            return []

        # build a set of conditions
        conditions = []
        if aid is not None:
            conditions.append("id == \"%s\"" % aid)
        if name is not None:
            conditions.append("name == \"%s\"" % name)
        if atype is not None:
            conditions.append("type == %d" % atype)
        cstr = ""
        for (i, c) in enumerate(conditions):
            cstr += c
            cstr += " AND " if i < len(conditions) - 1 else ""

        # execute the search and build an array of authors
        result = []
        for row in self.search("authors", cstr):
            author = DialogueAuthor.from_sqlite3(row)
            result.append(author)
        return result

    def save_conversation(self, convo: DialogueConversation):
        db_path = self.conf.dialogue_db

        # conversation metadata will be stored in a single table, whereas each
        # conversation's messages will be stored in separate tables. First, make
        # sure the 'conversations' table exists
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        table_fields_kept_visible = DialogueConversation.get_sqlite3_table_fields_kept_visible()
        table_definition = convo.get_sqlite3_table_definition(
            "conversations",
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        cur.execute(table_definition)

        # next, store the conversation's data in the conversation table
        convo_sqlite3 = convo.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cur.execute("INSERT OR REPLACE INTO conversations VALUES %s" %
                    str(convo_sqlite3))

        # get fields used to create the message table for this conversation
        mtable_name = DialogueConversation.to_sqlite3_table_name(convo.get_id())
        table_fields_kept_visible = DialogueMessage.get_sqlite3_table_fields_kept_visible()

        # now, for each message in the conversation table, save/update it
        for (i, msg) in enumerate(convo.messages):
            # on the first message, make sure the message table exists
            if i == 0:
                table_definition = msg.get_sqlite3_table_definition(
                    mtable_name,
                    fields_to_keep_visible=table_fields_kept_visible,
                    primary_key_field="id"
                )
                cur.execute(table_definition)

            # convert the message to an SQLite 3 tuple and insert/update it
            msg_sqlite3 = msg.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
            cmd = "INSERT OR REPLACE INTO %s VALUES %s" % (mtable_name, str(msg_sqlite3))
            cur.execute(cmd)
        con.commit()
        con.close()

        # determine if we need to prune the database
        now = datetime.now()
        prune_threshold = now.timestamp() - self.conf.dialogue_prune_rate
        if self.last_prune.timestamp() < prune_threshold:
            self.prune()

    # Searches for a conversation based on ID and/or start-time range. Returns
    # all matching conversations (or ALL conversations if no parameters are
    # specified).
    def search_conversation(self, cid=None, time_range=None):
        db_path = self.conf.dialogue_db
        if not os.path.isfile(db_path):
            return []

        # build a set of conditions
        conditions = []
        if cid is not None:
            conditions.append("id == \"%s\"" % cid)
        if time_range is not None:
            assert type(time_range) == list and len(time_range) >= 2, \
                   "time_range must a list of two timestamp ranges"
            conditions.append("time_start >= %d AND time_start <= %d" %
                              (time_range[0].timestamp(),
                               time_range[1].timestamp()))
        cstr = ""
        for (i, c) in enumerate(conditions):
            cstr += c
            cstr += " AND " if i < len(conditions) - 1 else ""

        # execute the search and build an array of conversations
        result = []
        for row in self.search("conversations", cstr):
            convo = DialogueConversation.from_sqlite3(row)
            result.append(convo)

        return result

    # Saves the provided message into its appropriate conversation table.
    def save_message(self, msg: DialogueMessage, cid=None):
        db_path = self.conf.dialogue_db

        # query for the conversation; it must already exist
        convos = self.search_conversation(cid=cid)
        convos_len = len(convos)
        if convos_len == 0:
            raise Exception("Unknown conversation ID: \"%s\"" % cid)
        assert convos_len == 1, "Multiple conversations found with ID: \"%s\"" % cid
        convo = convos[0]

        # determine the specific table
        mtable_name = DialogueConversation.to_sqlite3_table_name(convo.get_id())
        table_fields_kept_visible = DialogueMessage.get_sqlite3_table_fields_kept_visible()

        # make sure the table exists
        table_definition = msg.get_sqlite3_table_definition(
            mtable_name,
            fields_to_keep_visible=table_fields_kept_visible,
            primary_key_field="id"
        )
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(table_definition)

        # convert the message to an SQLite3 tuple and insert/update it
        msg_sqlite3 = msg.to_sqlite3_str(fields_to_keep_visible=table_fields_kept_visible)
        cmd = "INSERT OR REPLACE INTO %s VALUES %s" % (mtable_name, str(msg_sqlite3))
        cur.execute(cmd)

        # commit and close
        con.commit()
        con.close()

        # now that we have the message object updated (which lives in its
        # conversation-specific database), we also need to update the
        # conversation object as well. Why?
        #
        # When we serialize a conversation object, it contains the current
        # state of its messages. This means, when we load it back from SQLite3
        # and re-parse the encoded JSON, it will have the old message data. So,
        # we need to update the corresponding message object within `convo` and
        # save that as well.
        #
        # Iterate through the conversation object and replace the message
        # object.
        message_was_replaced = False
        for (i, m) in enumerate(convo.messages):
            if m.get_id() == msg.get_id():
                convo.messages[i] = msg
                message_was_replaced = True
                break
        # if the message did NOT already exist, add it to the end of the
        # conversation's messages
        if not message_was_replaced:
            convo.messages.append(msg)
        # save the updated conversation object
        self.save_conversation(convo)

        # determine if we need to prune the database
        now = datetime.now()
        prune_threshold = now.timestamp() - self.conf.dialogue_prune_rate
        if self.last_prune.timestamp() < prune_threshold:
            self.prune()

    # Searches all conversation tables for any messages with the matching
    # parameters. Reeturns a list of tuples containing:
    #
    #   (DialogueMessage, DialogueConversation)
    #
    # Where the `DialogueConversation` object corresponds to the conversation
    # that the message belongs to.
    def search_message(self,
                       mid=None,
                       aid=None,
                       time_range=None,
                       keywords=[],
                       telegram_chat_id=None,
                       telegram_message_id=None):
        db_path = self.conf.dialogue_db
        if not os.path.isfile(db_path):
            return []

        # retrieve all conversations via the conversation table and iterate
        # through them
        result = []
        convos = self.search_conversation()
        for convo in convos:
            # iterate through all messages in each conversation
            for msg in convo.messages:
                add = True
                # CHECK 1 - message ID
                if mid is not None:
                    add = add and msg.get_id().lower() == mid.lower()
                # CHECK 2 - author ID
                if aid is not None:
                    add = add and msg.author.get_id().lower() == aid.lower()
                # CHECK 3 - time range
                if time_range is not None:
                    assert type(time_range) == list and len(time_range) >= 2, \
                           "time_range must a list of two timestamp ranges"
                    ts = msg.timestamp.timestamp()
                    add = add and (ts >= time_range[0].timestamp() and
                                   ts <= time_range[1].timestamp())
                # CHECK 4 - keywords
                if len(keywords) > 0:
                    for word in keywords:
                        add = add and word.lower() in msg.content.lower()

                # CHECK 5 - telegram chat ID
                if telegram_chat_id is not None:
                    add = add and msg.telegram_chat_id == telegram_chat_id
                # CHECK 6 - telegram message ID
                if telegram_message_id is not None:
                    add = add and msg.telegram_message_id == telegram_message_id

                # add to the resulting list if all conditions pass
                if add:
                    result.append((msg, convo))
        return result

