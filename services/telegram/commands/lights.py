# Implements the /lights bot command.

# Imports
import os
import sys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from lumen.light import LightConfig, Light


# ================================= Helpers ================================== #
# Takes in a list of words and a list of Lights and returns those lights whose
# names and/or tags match all of the words.
def match_lights(words: list, lights: list):
    matches = []
    for light in lights:
        count = 0
        for word in words:
            if light.match_id(word) or light.match_tags(word):
                count += 1

        # if all words are matched, append the light
        if count == len(words):
            matches.append(light)
    return matches

# Turns the lights on.
def lights_on(service, message, args: list, session, lights: list):
    # if no third argument was given, we'll turn ALL lights on
    if len(args) < 3:
        successes = 0
        for light in lights:
            jdata = {"id": light.lid, "action": "on"}
            r = session.post("/toggle", payload=jdata)
            
            # if the operation succeeded, increment. Otherwise, send a message
            if r.status_code == 200 and session.get_response_success(r):
                successes += 1
                continue
            service.bot.send_message(message.chat.id, "I couldn't turn on %s." % light.lid)
        if successes > 0:
            service.bot.send_message(message.chat.id, "I turned on %d/%d lights." %
                                     (successes, len(lights)))
    
    # match the lights to the given arguments, then turn them each on
    matches = match_lights(args[2:], lights)
    for light in matches:
        jdata = {"id": light.lid, "action": "on"}
        r = session.post("/toggle", payload=jdata)

        # check the response for success
        if r.status_code == 200 and session.get_response_success(r):
            service.bot.send_message(message.chat.id, "I turned on %s." % light.lid)
        else:
            service.bot.send_message(message.chat.id, "I couldn't turn on %s." % light.lid)

# Turns the lights off.
def lights_off(service, message, args: list, session, lights: list):
    # if no third argument was given, we'll turn ALL lights off
    if len(args) < 3:
        successes = 0
        for light in lights:
            jdata = {"id": light.lid, "action": "off"}
            r = session.post("/toggle", payload=jdata)

            # if the operation succeeded, increment. Otherwise, send a message
            if r.status_code == 200 and session.get_response_success(r):
                successes += 1
                continue
            service.bot.send_message(message.chat.id, "I couldn't turn off %s." % light.lid)
        if successes > 0:
            service.bot.send_message(message.chat.id, "I turned off %d/%d lights." %
                                     (successes, len(lights)))
    
    # match the lights to the given arguments, then turn them each on
    matches = match_lights(args[2:], lights)
    for light in matches:
        jdata = {"id": light.lid, "action": "off"}
        r = session.post("/toggle", payload=jdata)

        # check the response for success
        if r.status_code == 200 and session.get_response_success(r):
            service.bot.send_message(message.chat.id, "I turned off %s." % light.lid)
        else:
            service.bot.send_message(message.chat.id, "I couldn't turn off %s." % light.lid)


# =================================== Main =================================== #
# Main function.
def command_lights(service, message, args: list):
    # create a HTTP session with lumen
    session = OracleSession(service.config.lumen_address,
                            service.config.lumen_port)
    try:
        r = session.login(service.config.lumen_auth_username,
                            service.config.lumen_auth_password)
    except Exception as e:
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't reach Lumen. "
                                "It might be offline.")
        return False
    
    # check the login response
    if r.status_code != 200:
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't authenticate with Lumen.")
        return False
    if not session.get_response_success(r):
        service.bot.send_message(message.chat.id,
                                "Sorry, I couldn't authenticate with Lumen. "
                                "(%s)" % session.get_response_message(r))
        return False

    # retrieve a list of lights and convert them into objects
    r = session.get("/lights")
    lights = []
    try:
        ldata = session.get_response_json(r)
        for l in ldata:
            lconf = LightConfig()
            lconf.parse_json(l)
            lights.append(Light(lconf))
    except Exception as e:
        service.bot.send_message(message.chat.id,
                                 "Sorry, I couldn't retrieve light data. (%s)" % e)
    
    # if no other arguments were specified, we'll generate a list of names
    # for the lights around the house
    if len(args) <= 1:
        msg = "<b>All connected lights</b>\n\n"
        for light in lights:
            msg += "• <code>%s</code> - %s\n" % \
                   (light.lid, light.description)
        service.bot.send_message(message.chat.id, msg, parse_mode="HTML")
        return True

    # if the second argument is "on", we'll turn lights on
    second = args[1].strip().lower()
    if second == "on":
        return lights_on(service, message, args, session, lights)

    # if the second argument is "off", we'll turn lights off
    if second == "off":
        return lights_off(service, message, args, session, lights)

    msg = "I'm not sure what you meant."
    service.bot.send_message(message.chat.id, msg)

