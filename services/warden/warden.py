#!/usr/bin/python3
# My service for doing some basic network monitoring.

# Imports
import os
import sys
import json
import flask
import subprocess
import socket
import ipaddress
import time
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI

# Service imports
from device import Device, DeviceConfig
from client import Client


# =============================== Config Class =============================== #
class WardenConfig(ServiceConfig):
    # Constructor.
    def __init__(self):
        super().__init__()
        # create lumen-specific fields to append to the existing service fields
        fields = [
            ConfigField("devices",          [list],     required=True),
            ConfigField("refresh_rate",     [int],      required=False,     default=120),
            ConfigField("ping_timeout",     [float],    required=False,     default=0.25),
            ConfigField("ping_tries",       [int],      required=False,     default=1),
            ConfigField("sweep_threshold",  [int],      required=False,     default=3600),
            ConfigField("initial_sweeps",   [int],      required=False,     default=1)
        ]
        self.fields += fields


# ============================== Service Class =============================== #
class WardenService(Service):
    # Constructor.
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = WardenConfig()
        self.config.parse_file(config_path)

        # make a few config assertions
        assert self.config.refresh_rate > 0, "the refresh rate must be greater than 0"
        assert self.config.ping_timeout > 0.0, "the ping timeout must be greater than 0"
        assert self.config.ping_tries > 0, "the ping try count must be greater than 0"

        # parse out the individual devices from the "devices" field
        self.devices = []
        for ddata in self.config.devices:
            dc = DeviceConfig()
            dc.parse_json(ddata)
            self.devices.append(Device(dc))

        # the service will keep a cache of IP/MAC addresses, but it starts as an
        # empty dictionary
        self.cache = {}
        self.last_sweep = datetime.fromtimestamp(0)


    # Overridden main function implementation.
    def run(self):
        super().run()

        # Helper function to determine if it's time for a sweep.
        def can_sweep():
            time_to_sweep_threshold = now.timestamp() - self.last_sweep.timestamp()
            can_sweep = time_to_sweep_threshold >= self.config.sweep_threshold
            return can_sweep

        # get our own IP address
        self.addr = self.get_address()
        self.log.write("Warden's IP address: %s" % self.addr)

        # before entering the main loop, we'll sweep the network a number of
        # times to build up the cache of connected devices
        for i in range(self.config.initial_sweeps):
            self.log.write("Performing initial network sweep (%d/%d)" %
                           ((i + 1), self.config.initial_sweeps))
            self.sweep()

        # dump out the current state of the cache
        self.log.write("Initial cache entries:")
        for entry in self.cache:
            self.log.write(" - %s" % self.cache[entry])

        # loop forever
        while True:
            now = datetime.now()
            pfx = "[%s]" % now.strftime("%Y-%m-%d %H:%M:%S")

            # if we're past the sweep threshold, sweep the network
            if can_sweep():
                self.log.write("%s Sweeping the network..." % pfx)
                self.sweep()
                for entry in self.cache:
                    self.log.write(" - %s" % self.cache[entry])

            # iterate across all clients stored in the cache
            for addr in self.cache:
                client = self.cache[addr]

                # ping the client and update if it responds
                ping_tries = self.config.ping_tries * 2
                if self.ping(client.ipaddr, tries=ping_tries):
                    self.log.write("%s Client \"%s\" is responding." %
                                   (pfx, client))
                    client.update()

            # sleep for the specified amount of seconds
            time.sleep(self.config.refresh_rate)


    # ------------------------------- Caching -------------------------------- #
    # Returns the matching Client object, or None.
    def cache_get(self, macaddr: str):
        macaddr = macaddr.lower()
        return None if macaddr not in self.cache else self.cache[macaddr]

    # Takes a MAC address and adds an entry to the cache. The Client object is
    # returned.
    def cache_set(self, macaddr: str):
        macaddr = macaddr.lower()
        if macaddr in self.cache:
            return self.cache[macaddr]
        self.cache[macaddr] = Client(macaddr)
        return self.cache[macaddr]


    # --------------------------------- API ---------------------------------- #
    # Attempts to determine (and return) the IP address of the service.
    def get_address(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        addr = None
        try:
            # connect to some arbitrary address
            sock.connect(("192.168.0.1", 1))
            addr = sock.getsockname()[0]
        except:
            addr = "192.168.0.100"

        # close the socket and return the address
        sock.close()
        return addr

    # Gets the service's IP address with the "/24" netmask included at the end.
    def get_netmask(self):
        args = ["ip", "-o", "-f", "inet", "addr", "show"]
        result = subprocess.run(args, capture_output=True)
        assert len(result.stderr) == 0, "\"ip\" command produced error messages"

        # parse the output, line-by-line, for the correct value
        addr = self.get_address()
        for line in result.stdout.decode().split("\n"):
            # remove extra whitespace and skip empty lines
            line = line.strip()
            if len(line) == 0:
                continue

            # split into pieces and skip lines that don't have enough
            pieces = line.split()
            if len(pieces) < 4:
                continue

            # extract the net mask and compare against our address
            mask = pieces[3]
            if addr in mask:
                return mask

        # we shouldn't reach here...
        assert False, "failed to look up netmask"

    # Returns a list of all the valid addresses on the same network as the
    # service.
    def get_all_addresses(self):
        network = ipaddress.IPv4Network(self.get_netmask(), strict=False)
        result = []
        for addr in list(network.hosts()):
            result.append(str(addr))
        return result

    # Sweeps the entire range of IP addresses in the same subnet as the
    # service's IP address. Returns dictionary of IP addresses and MAC
    # addresses corresponding to the clients that responded.
    def sweep(self):
        self.last_sweep = datetime.now()
        # get all addresses, and our own address
        addr = self.get_address()
        addresses = self.get_all_addresses()

        # ping all addresses
        up_addrs = []
        log_msg = "Trying address %s"
        for (i, addr) in enumerate(addresses):
            # write a message to the log
            log_msg_end = "" if i < len(addresses) - 1 else "\n"
            self.log.write(log_msg % addr, begin="\r", end=log_msg_end)

            entry = {"ipaddr": None, "macaddr": None}

            # ping and save the address if the ping succeeded
            if self.ping(addr):
                entry["ipaddr"] = addr
                self.log.write(" - UP", show_prefix=False)
            else:
                continue

            # perform an ARP lookup to get the client's MAC address
            macaddr = self.arp(addr, do_ping=False)
            macaddr = macaddr.lower() if macaddr else macaddr
            entry["macaddr"] = macaddr
            up_addrs.append(entry)

            # add this to the cache, or update the existing entry
            if macaddr:
                client = self.cache_set(macaddr)
                client.update(ipaddr=addr)
        return up_addrs

    # Attempts to do various nmap ping strategies to identify if a host is up or
    # not. Used by `self.ping()`. Returns True if the host is up, False
    # otherwise.
    # https://nmap.org/book/host-discovery-techniques.html
    def ping_nmap(self, address: str, timeout: float, tries: int, pingtype=None):
        tmpfile = ".warden.nmap.out"

        # select a ping type to attempt
        pingtype = "pe" if pingtype is None else pingtype.strip().lower()
        ptarg = "-PE" # default is an ICMP echo request (i.e. the 'ping' tool)
        types = {
            "pe": "-PE",    # ICMP echo request (default)
            "pp": "-PP",    # ICMP timestamp query packet
            "pm": "-PM",    # ICMP address mask query
            "ps": "-PS",    # TCP SYN ping
            "pa": "-PA",    # TCP ACK ping
            "pu": "-PU"     # UDP ping
        }
        for t in types:
            if pingtype == t:
                ptarg = types[t]
                break

        # try a number of times to ping the host
        for i in range(tries):
            # increase timeout for each trial
            timeout = timeout + (i * timeout)

            # delete the temporary file, if it exists from a previous run
            # that was interrupted or prevented from deleting it
            if os.path.isfile(tmpfile):
                os.remove(tmpfile)

            # create program arguments and launch a subprocess
            args = [
                "nmap",
                "-sn",          # disable port discovery - ping scan only
                ptarg,          # selected ping type
                address,
                "--max-rtt-timeout", str(timeout),
                "-oG", tmpfile  # write greppable output to file
            ]
            result = subprocess.run(args,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)

            # if the file wasn't created, something went wrong
            if not os.path.isfile(tmpfile):
                return False

            # read the file's contents line by line
            host_is_up = False
            with open(tmpfile, "r") as fp:
                for line in fp:
                    line = line.strip().replace("\n", "")
                    # skip any lines that are comments
                    if line.startswith("#"):
                        continue

                    # otherwise, look for the IP address and the 'Up' keyword
                    if address in line and "up" in line.lower():
                        host_is_up = True
                        break

            # delete the temporary file
            if os.path.isfile(tmpfile):
                os.remove(tmpfile)

            # return according to what we found in the output
            return host_is_up

    # Pings a given IP address and returns True if the host is up.
    # This may attempt multiple pings to reduce inaccuracy or unreturned pings
    # due to network latency.
    def ping_classic(self, address: str, timeout=None, tries=None):
        # establish defaults if none were given
        timeout = self.config.ping_timeout if timeout is None else timeout
        tries = self.config.ping_tries if tries is None else tries

        # try a number of times to ping the ip address
        for i in range(tries):
            # the more times we try, the longer the timeout we'll allow
            timeout = timeout + (i * timeout)

            # spawn a ping process and return the exit code
            args = [
                "ping", address,
                "-c", "1",
                "-t", "8",
                "-W", str(timeout)
            ]
            result = subprocess.run(args,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)

            # if we didn't get a zero return value, we'll try again
            if result.returncode != 0:
                continue
            return True
        return False

    # Attempts a variety of different pinging techniques to determine if a host
    # is up and responding.
    def ping(self, address: str, timeout=None, tries=None):
        # establish limits and run the nmap helper
        timeout = self.config.ping_timeout if timeout is None else timeout
        tries = self.config.ping_tries if tries is None else tries

        # ATTEMPT 1: Classic 'ping' utility
        if self.ping_classic(address, timeout=timeout, tries=tries):
            return True
        # ATTEMPT 2: nmap default echo request (basically the same as 'ping')
        elif self.ping_nmap(address, timeout, tries):
            return True
        # ATTEMPT 3-N: various nmap ping styles
        else:
            for pt in ["pp", "pm", "ps", "pa", "pu"]:
                if self.ping_nmap(address, timeout, tries, pingtype=pt):
                    return True

        # if we reach here without returning true, we'll assume the host is
        # offline
        return False

    # Look up the MAC address of the given IP address. If 'do_ping' is True, the
    # address will be pinged beforehand (to fill up the ARP cache).
    # The MAC address is returned as a string.
    def arp(self, address: str, do_ping=True):
        # if specified, ping the address
        if do_ping:
            assert self.ping(address), "the address did not respond to a ping"

        # invoke the 'arp' system command
        args = ["arp", "-n", address]
        result = subprocess.run(args, capture_output=True)
        assert len(result.stderr) == 0, "arp produced error messages"

        for line in result.stdout.decode().split("\n"):
            line = line.strip()
            if len(line) == 0 or "no entry" in line:
                continue

            # look for the line that contains the given IP address
            pieces = line.split()
            if pieces[0] == address:
                macaddr = pieces[2].lower()
                # update the cache entry
                client = self.cache_set(macaddr)
                client.update(ipaddr=address)
                return pieces[2]
        return None


# ============================== Service Oracle ============================== #
class WardenOracle(Oracle):
    # Endpoint definition function.
    def endpoints(self):
        super().endpoints()

        # This endpoint returns a list of all configured devices. The response
        # only contains static information that was defined in the configuration
        # file.
        @self.server.route("/devices", methods=["GET"])
        def endpoint_devices():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # retrieve all devices and build a JSON dictionary to return
            result = []
            for device in self.service.devices:
                result.append(device.config.to_json())
            return self.make_response(payload=result)

        # This endpoint retrieves all clients stored in the warden's cache.
        @self.server.route("/clients", methods=["GET"])
        def endpoint_clients():
            if not flask.g.user:
                return self.make_response(rstatus=404)

            # retrieve all clients from the warden's cache and build a JSON
            # dictionary to return.
            result = []
            named_devices_already_added = {}
            for addr in self.service.cache:
                c = self.service.cache[addr]
                jdata = c.to_json()
                # cross-reference with our list of devices and see if this
                # device has a name (if so, add it)
                for device in self.service.devices:
                    # if we've already seen this named device, skip it (this
                    # could happen if a device is using multiple MAC addresses)
                    if device.config.name in named_devices_already_added:
                        continue

                    # do any of the configured MAC addresses match this addr? If
                    # so, add the named device to the JSON
                    for macaddr in device.config.macaddrs:
                        if macaddr.lower() == c.macaddr.lower():
                            jdata["name"] = device.config.name
                            jdata["tags"] = device.config.tags
                            named_devices_already_added[device.config.name] = True
                            break
                result.append(jdata)
            self.log.write("Returning a list of %d connected clients to %s" %
                           (len(result), flask.g.user.config.username))
            return self.make_response(payload=result)


# =============================== Runner Code ================================ #
if __name__ == "__main__":
    cli = ServiceCLI(config=WardenConfig, service=WardenService, oracle=WardenOracle)
    cli.run()

