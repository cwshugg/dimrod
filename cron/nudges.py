#!/usr/bin/env python3
# This task sends random, gentle nudges for better habits, reducing stress, and
# better mental health.

# Imports
import os
import sys
import json
import random
from datetime import datetime

# Enable import from the main directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from services.lib.oracle import OracleSession
import services.lib.dtu as dtu

# Service configs
telegram_config_path = os.path.join(pdir, "services/telegram/cwshugg_telegram.json")
telegram_chat_names = ["cwshugg"]
speaker_config_path = os.path.join(pdir, "services/speaker/cwshugg_speaker.json")


# ================================= Helpers ================================== #
# Parses a config file and returns the JSON data.
def get_config(path: str):
    fp = open(path, "r")
    jdata = json.load(fp)
    fp.close()
    return jdata

# Returns an OracleSession object that's been authenticated.
def get_session(conf: dict):
    s = OracleSession(conf["oracle_addr"], conf["oracle_port"])
    user = conf["oracle_auth_users"][0]
    s.login(user["username"], user["password"])
    return s

def randbool(chance: float):
    # compute a low and high value to use for random range generation, so we can
    # generate a number and test it for the given percent chance
    maxval = 100000
    low = 0
    high = int(maxval * chance)
    return random.randrange(maxval) in range(low, high)


# ================================== Nudges ================================== #
# Each of these nudge functions should return a message or None, depending on
# various conditions and/or randomness.

# Weekend nudges.
def nudge_weekend_day(dt):
    if not dtu.is_weekend(dt):
        return None
    
    # do a quick random-chance calculation, and only proceed if it succeeds
    if not randbool(0.001):
        return None

    # otherwise, choose a random message and return it
    ideas = [
        "If the weather is appropriate, I'd suggest going for a walk outside.",
        "If you're bored, I'd suggest reading a book.",
        "If you're board, I'd suggest going to the gym or doing a home workout.",
        "I suggest taking a moment to stretch a little."
    ]
    
    # add some seasonal ideas
    if not dtu.is_winter(dt):
        ideas.append("If the weather is nice today, you should go for a bike ride.")

    # choose one at random and return it
    return random.choice(ideas)

# Weekday nudges.
def nudge_weekday_day(dt):
    if not dtu.is_weekday(dt) or dtu.is_evening(dt):
        return None

    # do a quick random-chance calculation, and only proceed if it succeeds
    if not randbool(0.001):
        return None

    ideas = [
        "If the weather is appropriate, I'd suggest going for a walk outside."
        "I suggest taking a moment to stretch a little."
    ]

    # add some seasonal ideas
    if dtu.is_summer(dt):
        ideas.append("If the weather is hot today, I suggest going for a swim in the pool.")

    # if it's during working hours, suggest some other ideas
    if dtu.is_workhours(dt):
        ideas = [
            "I suggest getting a hot cup of tea to refresh yourself.",
            "I suggest eating a light snack to give yourself a little energy.",
            "I suggest going for a quick walk to give yourself a quick break.",
            "I suggest finding a different place to work in the office, to change the scenery.",
            "Try asking some friends to go out to lunch during the work day.",
            "Try the 4-7-8 breathing technique.",
            "Try taking a moment to stretch.",
            "Feeling a little tired? Try taking a quick shower.",
            "Pause for a second. You're doing a great job. Take some deep breaths.",
            "Take a second to check your posture. Make sure you're not slouching. Sit up straight and relax your shoulders."
        ]

    return random.choice(ideas)

# Weekday evening nudges.
def nudge_weekday_night(dt):
    if not dtu.is_weekday(dt) or not dtu.is_evening(dt):
        return None

    # do a quick random-chance calculation, and only proceed if it succeeds
    if not randbool(0.001):
        return None

    ideas = [
        "I think you should try to stop looking at screens an hour before bedtime.",
        "I suggest a little time to read a book tonight before bed.",
        "Try going to bed a little earlier than last night."
    ]

# Date idea nudges.
def nudge_dates(dt):
    if not randbool(0.0005):
        return None
    
    ideas = []

    # some adventurous ideas
    ideas += [
        "Here's an adventurous date idea: going go karting.",
        "Here's an adventurous date idea: going to a high ropes course.",
        "Here's an adventurous date idea: going to a zip line course.",
        "Here's an adventurous date idea: going to a driving range.",
        "Here's an adventurous date idea: going to play mini golf.",
        "Here's an adventurous date idea: go swimming or boating on the lake.",
        "Here's an adventurous date idea: go hiking.",
        "Here's an adventurous date idea: go camping.",
        "Here's an adventurous date idea: go on a ghost tour."
        "Here's an adventurous date idea: go on a hot air balloon ride."
    ]

    # some other ideas
    ideas += [
        "Here's a fun date idea: go stargzing.",
        "Here's a fun date idea: have a board game night at home.",
        "Here's a fun date idea: have a video game night at home.",
        "Here's a fun date idea: have a movie night at home.",
        "Here's a fun date idea: go rollerskating.",
        "Here's a fun date idea: go to a barcade.",
        "Here's a fun date idea: go to an antique store or auction to find a project for home."
    ]

    # experiences
    ideas += [
        "Here's a fun date idea: go to a magic show.",
        "Here's a fun date idea: go to a paint-and-sip class.",
        "Here's a fun date idea: go to a planetarium show.",
        "Here's a fun date idea: go to an aquarium.",
        "Here's a fun date idea: go to the zoo.",
        "Here's a fun date idea: go to a nearby museum."
    ]

    # some restaurants
    ideas += [
        "Here's a nice restaurant for date night: Cheesecake Factory.",
        "Here's a nice restaurant for date night: The Melting Pot.",
        "Here's a nice restaurant for date night: Olive Garden.",
        "Here's a nice restaurant for date night: A Japanese Steakhouse, where they make the food right in front of you."
    ]

    # add some spring ideas
    if dtu.is_spring(dt):
        ideas += [
            "Here's a fun spring date idea: go strawberry picking.",
            "Here's a fun spring date idea: go on a picnic.",
            "Here's a fun spring date idea: go to a baseball game."
        ]

    if dtu.is_summer(dt):
        ideas += [
            "Here's a fun summer date idea: go to a drive-in movie.",
            "Here's a fun summer date idea: go to an outdoor movie.",
            "Here's a fun summer date idea: go to the beach for a day."
        ]

    if dtu.is_fall(dt):
        ideas += [
            "Here's a fun fall date idea: go to a football game.",
            "Here's a fun fall date idea: go to a fall festival."
        ]

        # halloween ideas
        if dt.month == 10:
            ideas += [
                "Here's a fun Halloween date idea: go to a pumpkin patch.",
                "Here's a fun Halloween date idea: go to a haunted corn maze.",
                "Here's a fun Halloween date idea: go to a haunted house."
            ]

    # add some winter ideas
    if dtu.is_winter(dt):
        ideas += [
            "Here's a fun winter date idea: go ice skating.",
        ]
        
        # christmas ideas
        if dt.month == 12:
            ideas += [
                "Here's a fun Christmas date idea: go driving and look for Christmas lights and decorations.",
                "Here's a fun Christmas date idea: have a Christmas movie night.",
                "Here's a fun Christmas date idea: go to a Christmas concert.",
                "Here's a fun Christmas date idea: go to a Christmas market or craft fair.",
                "Here's a fun Christmas date idea: go on a horse-drawn carriage ride through a Christmas-themed town."
            ]
    
    return random.choice(ideas)


# =================================== Main =================================== #
# Main function.
def main():
    # ---------------------------- Nudge Decision ---------------------------- #
    # get current datetime and create a randomly-shuffled list of the nudge
    # functions to call
    now = datetime.now()
    nudge_funcs = [
        nudge_weekend_day,
        nudge_weekday_day,
        nudge_weekday_night,
        nudge_dates
    ]
    random.shuffle(nudge_funcs)
    
    # iterate through all nudge functions and pick one out
    msg = None
    for nf in nudge_funcs:
        # run the function and stop if a message is returned
        msg = nf(now)
        if msg is not None:
            print("Found nudge from %s: \"%s\"" % (nf.__name__, msg))
            break

    # if we found a message, we'll send it below. Otherwise, stop here
    if msg is None:
        return

    # ------------------------- Dialogue Integration ------------------------- #
    # set up a speaker session
    sc = get_config(speaker_config_path)
    ss = get_session(sc)
    
    # build and send a payload to have the phrase reworded
    speaker_data = {"message": msg}
    r = ss.post("/reword", payload=speaker_data)
    if OracleSession.get_response_success(r):
        # extract the returned JSON payload and the reworded version of the
        # original message
        payload = OracleSession.get_response_json(r)
        if "message" in payload:
            msg = str(payload["message"])

    # --------------------------- Telegram Message --------------------------- #
    # setup telegram session
    tc = get_config(telegram_config_path)
    ts = get_session(tc)
    
    # get all telegram chats the bot has whitelisted
    r = ts.get("/bot/chats")
    chats = ts.get_response_json(r)

    # find my private chat and send a message
    for chat in chats:
        for cname in telegram_chat_names:
            if cname.lower() in chat["name"].lower():
                print("Sending message to Telegram chat %s (%s)." %
                      (chat["id"], chat["name"]))
                mdata = {
                    "chat": chat,
                    "message": msg
                }
                ts.post("/bot/send", payload=mdata)

# Runner code
if __name__ == "__main__":
    sys.exit(main())

