# This module defines class(es) representing a single network-connected device.

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Class that represents a single known device's information, which can be
# provided at runtime via a config file.
class KnownDeviceConfig(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",             [str],      required=True),
            ConfigField("macaddrs",         [list],     required=True),
            ConfigField("tags",             [list],     required=False, default=[]),
        ]

    def post_parse_init(self):
        # sanitize MAC addresses
        self.macaddrs = [
            mac.strip().lower().replace("-", ":").replace(".", ":")
            for mac in self.macaddrs
        ]

        # sanitize tags
        self.tags = [tag.strip().lower() for tag in self.tags]

# Class that represents a device's unique hardware address.
class DeviceHardwareAddress(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("macaddr",          [str],      required=True),
            ConfigField("vendor",           [str],      required=False, default=None),
        ]

# Class that represents a device's network connection address.
class DeviceNetworkAddress(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("ipaddr",           [str],      required=True),
        ]

# Class that represents a device. It has a configuration (`DeviceConfig`) and
# can have a `DeviceAddress`.
class Device(Config):
    # Constructor.
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("known_device",     [KnownDeviceConfig],        required=False, default=None),
            ConfigField("hw_addr",          [DeviceHardwareAddress],    required=False, default=None),
            ConfigField("net_addr",         [DeviceNetworkAddress],     required=False, default=None),
            ConfigField("last_seen",        [datetime],                 required=False, default=None),
        ]

    # Assigns a known device configuration to this device.
    def set_known_device(self, known_dev: KnownDeviceConfig):
        self.known_device = known_dev

    # Sets the device's hardware address.
    def set_hardware_address(self, hw_addr: DeviceHardwareAddress):
        self.hw_addr = hw_addr

    # Sets the device's network address.
    def set_network_address(self, net_addr: DeviceNetworkAddress):
        self.net_addr = net_addr

    # Sets the time the device was last seen on the network.
    def set_last_seen(self, timestamp: datetime):
        self.last_seen = timestamp

    def to_str_brief(self):
        name = "Unknown Device"
        if self.known_device:
            name = self.known_device.name
        macaddr = "Unknown MAC"
        if self.hw_addr:
            macaddr = self.hw_addr.macaddr
        ipaddr = "Unknown IP"
        if self.net_addr:
            ipaddr = self.net_addr.ipaddr
        last_seen_str = "Never"
        if self.last_seen:
            last_seen_str = self.last_seen.strftime("%Y-%m-%d %H:%M:%S")
        return "%s | MAC: %s | IP: %s | Last Seen: %s" % (name, macaddr, ipaddr, last_seen_str)

