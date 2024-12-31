# This implement intent parsing for light-related dialogue.

import os
import sys
import abc
from adapt.intent import IntentBuilder
from adapt.engine import IntentDeterminationEngine

# Enable import from the PARENT and GRANDPARENT directories
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)
gpdir = os.path.dirname(pdir)
if gpdir not in sys.path:
    sys.path.append(gpdir)

# Local imports
from lib.config import Config, ConfigField
from lib.oracle import OracleSession, OracleSessionConfig

# Speaker imports
from action import DialogueActionConfig, DialogueAction


# =============================== Config Class =============================== #
class DialogueActionConfig_Lights(DialogueActionConfig):
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("lumen",        [OracleSessionConfig],  required=True),
        ]


class DialogueAction_Lights(DialogueAction):
    # Constructor. Takes in a config object.
    def __init__(self, config_data: dict):
        super().__init__(config_data)
        self.config = DialogueActionConfig_Lights()
        self.config.parse_json(config_data)
    
    # ----------------------------- Lumen Comms ------------------------------ #
    # Gets and returns a new session with lumen.
    def lumen_session(self):
        # createa  session and log in
        s = OracleSession(self.config.lumen)
        r = s.login()
        assert OracleSession.get_response_success(r), "failed to authenticate with lumen"
        return s

    # Talks to lumen to retrieve a JSON list of lights
    def lumen_lights(self):
        s = self.lumen_session()
        r = s.get("/lights")
        assert OracleSession.get_response_success(r), "failed to retrieve lights from lumen"
        return OracleSession.get_response_json(r)
    
    # ---------------------------- Intent Parsing ---------------------------- #
    def engine_init(self):
        self.engine = IntentDeterminationEngine()

        # establish keywords to use for recognizing lumen actions
        keywords = [
            "light", "lights", "bulb", "switch", "outlet", "lamp", "LED",
            "power", "cord", "strip", "smart", "plug",
            "turn", "fire"
        ]
        for word in keywords:
            self.engine.register_entity(word, "light_keyword")

        # retrieve a list of lights from lumen and use the tags as words for the
        # engine for type recognition
        lights = self.lumen_lights()
        tags = []
        for entry in lights:
            # add each tag to the master tag list
            for tag in entry["tags"]:
                tl = tag.lower()
                if tl not in tags:
                    tags.append(tl)
                    self.engine.register_entity(tl, "light_tag")

        # build a list of action keywords for turning lights on
        light_on_keywords = [
            "on", "enable", "up"
        ]
        for word in light_on_keywords:
            self.engine.register_entity(word, "light_action_on")
    
        # build a list of action keywords for turning lights off
        light_off_keywords = [
            "off", "disable", "kill", "out", "down"
        ]
        for word in light_off_keywords:
            self.engine.register_entity(word, "light_action_off")

        # finally, create an intent builder and register it to the engine
        intent = IntentBuilder("light_toggle_intent") \
                    .require("light_keyword") \
                    .optionally("light_tag") \
                    .optionally("light_action_on") \
                    .optionally("light_action_off") \
                    .build()
        self.engine.register_intent_parser(intent)
    
    def engine_process(self, text: str):
        successes = 0
        for intent in self.engine.determine_intent(text):
            # if we exceed the confidence threshold, use it
            if intent.get("confidence") >= self.config.confidence_threshold:
                # based on the intent, invoke the correct helper function
                if intent["intent_type"].lower() == "light_toggle_intent":
                    return self.process_light_toggle(intent)

        return None
                    

    # ------------------------------- Helpers -------------------------------- #
    # Invoked when a light-toggle intent is found.
    def process_light_toggle(self, intent: dict):
        # retrieve the "on" or "off" keyword to determine what action to take
        action = "on" if "light_action_on" in intent else "off"

        # grab the tags (if any) from the intent
        tags = [] if "light_tag" not in intent else [intent["light_tag"]]

        # grab a list of lumen lights and search through them for matching tags
        lights = self.lumen_lights()
        matches = []
        for light in lights:
            # search through ALL tags and only save the lights where all tags
            # are matching
            tag_matches = 0
            for tag in tags:
                tag_matches += int(tag.strip().lower() in light["tags"])
            
            # if all tags were matched, save this light
            if tag_matches == len(tags):
                matches.append(light)

        # for each match, tell lumen to carry out the specified action
        s = self.lumen_session()
        for light in matches:
            s.post("/toggle", payload={"id": light["id"], "action": action})

        # craft and return a response message
        if len(matches) > 0:
            return "I turned %s %d device%s." % \
                   (action, len(matches), "s" if len(matches) > 0 else "")
        return "I couldn't find any matching devices to turn %s." % action

