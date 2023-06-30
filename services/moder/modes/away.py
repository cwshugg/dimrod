# The Away Mode. This should activate when nobody's home.

# Imports
import os
import sys
import random
from datetime import datetime

# Enable import from the PARENT and GRANDPARENT directories
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)
gpdir = os.path.dirname(pdir)
if gpdir not in sys.path:
    sys.path.append(gpdir)

# Imports
from lib.oracle import OracleSession
from mode import Mode

class Mode_Away(Mode):
    def __init__(self, service, priority=None):
        super().__init__(service, priority=priority)
        self.name = "away"
        self.ws = None
        self.ls = None

    # --------------------------- Network Helpers ---------------------------- #
    # Function that pings Warden (the network service) to determine what devices
    # are online. This information is used to determine who's at home (i.e.
    # (whose cell phones are connected to the network).
    def devices(self):
        # use the warden information in the config file to reach out and
        # get an authenticated session
        if self.ws is None:
            self.ws = OracleSession(self.config.warden_address,
                               self.config.warden_port)
            r = self.ws.login(self.config.warden_auth_username,
                         self.config.warden_auth_password)

        # with the session, ping warden's /clients endpoint to retrieve all
        # client connection information
        r = self.ws.get("/clients")
        if not OracleSession.get_response_success(r):
            raise Exception("failed to retrieve device information from warden")
        
        # retrieve the client data returned by warden and process them
        clients = OracleSession.get_response_json(r)
        result = []
        for client in clients:
            # grab the MAC address and name (if it has a name)
            client_mac = client["macaddr"].lower()
            client_name = "" if "name" not in client else client["name"].lower()
            client_time = 0 if "last_seen" not in client else int(client["last_seen"])
                
            # iterate through the devices specified in the config; these are the
            # ones were care about in "away mode"
            for text in self.config.moder_mode_away_devices:
                t = text.lower()
                # the MAC address must be an exact match, or the text must be
                # contained by a device's name
                if t == client_mac or t in client_name:
                    result.append({
                        "name": client_name,
                        "macaddr": client_mac,
                        "last_seen": client_time
                    })
        
        # return the list of relevant devices
        return result
    
    # Uses the configured thresholds and devices names/macaddrs to find and
    # return the most-recently-online relevant device and returns its
    # information.
    def latest_device(self):
        devices = self.devices()
        now = datetime.now()

        # iterate over the devices and determine the one that was most recently
        # online (the 'last_seen' field closest to NOW)
        latest_dev = None
        latest_diff = now.timestamp()
        for dev in devices:
            diff = now.timestamp() - dev["last_seen"]
            # update the smallest diff, and save a reference to the device while
            # we're at it
            if diff < latest_diff:
                latest_diff = diff
                latest_dev = dev
        
        if latest_dev is None:
            return None
        latest_dev["last_seen_diff"] = latest_diff
        return latest_dev

    # --------------------------- Lighting Helpers --------------------------- #
    # Finds and returns configured groups of lights available for toggling.
    def light_groups(self):
        # create a lumen session and ask for all lights
        if self.ls is None:
            self.ls = OracleSession(self.config.lumen_address,
                               self.config.lumen_port)
            r = self.ls.login(self.config.lumen_auth_username,
                         self.config.lumen_auth_password)

        # with the session, ping lumen's /lights endpoint to retrieve all
        # light information
        r = self.ls.get("/lights")
        if not OracleSession.get_response_success(r):
            raise Exception("failed to retrieve light information from lumen")
        
        # get the JSON payload and iterate through them to find matches and
        # build up a list of light groups
        lights = OracleSession.get_response_json(r)
        groups = []
        for ldata in self.config.moder_mode_away_lights:
            taglist = ldata["tags"]
            group = {"chance": ldata["chance"], "lights": []}
            for light in lights:
                our_tags = [t.strip().lower() for t in taglist]
                light_tags = [t.strip().lower() for t in light["tags"]]
                # count the number of matching tags
                matches = 0
                for tag in our_tags:
                    matches += int(tag in light_tags)

                # if all tags matched, OR one of the configured tags is the name
                # of the light itself, add it to this group
                if matches == len(taglist) or light["id"].strip().lower() in our_tags:
                    group["lights"].append(light)

            # if the group has at least one light in it, add it to the main list
            if len(group["lights"]) > 0:
                groups.append(group)
        return groups
        
    def light_toggle(self, group):
        # with the groups, choose one at random and run the numbers (using its
        # chance) to determine if we're going to toggle it or not
        chance_max = 1000000.0
        chance_threshold = chance_max * float(group["chance"])
        if float(random.randrange(0, chance_max)) >= chance_threshold:
            # not going to toggle! skip
            return
    
        # randomly choose 'on' or 'off'
        action = random.choice(["on", "off"])
        self.log("Toggling light group with %d lights %s." %
                 (len(group["lights"]), action))
            
        # apply the action to all lights in the group
        for light in group["lights"]:
            pyld = {"id": light["id"], "action": action}
            self.ls.post("/toggle", payload=pyld)

    def light_cleanup(self):
        # retrieve all light groups
        groups = self.light_groups()
        # turn all light groups off
        for group in groups:
            self.log("Clean-up: turning off light group with %d lights." % len(group["lights"]))
            for light in group["lights"]:
                pyld = {"id": light["id"], "action": "off"}
                self.ls.post("/toggle", payload=pyld)


    # ---------------------------- Main Functions ---------------------------- #
    def priority(self):
        if self.priority_override is not None:
            return self.priority_override

        ldev = self.latest_device()
        
        # using the latest diff time, determine if someone is "home" or not by
        # using the configured threshold
        if ldev is not None and \
           ldev["last_seen_diff"] < self.config.moder_mode_away_device_threshold:
            # if the amount of time since it was last pinged is LESS than the
            # configured threshold, we still consider someone to be home,
            # so we return a priority of 0 (indicating we DON'T want to activate
            # this mode)
            return 0
        else:
            # otherwise, we consider nobody to be home, so we return a priority
            # of GREATER than zero (indicating we DO want to activate this mode)
            return 2

    def is_complete(self):
        # grab the latest device and determine if it's currently considered
        # online. If it is, the mode is complete
        ldev = self.latest_device()
        if ldev is not None and \
           ldev["last_seen_diff"] < self.config.moder_mode_away_device_threshold:
            # log a message and return True
            device_str = ldev["macaddr"] if "name" not in ldev else ldev["name"]
            self.log("Device \"%s\" was last seen online %d seconds ago. "
                     "Somebody is home. Mode \"%s\" is complete." %
                     (device_str, ldev["last_seen_diff"], self.name))
            return True
        else:
            return False

    def step(self):
        groups = self.light_groups()
        group = random.choice(groups)
        self.light_toggle(group)

    def cleanup(self):
        self.light_cleanup()

