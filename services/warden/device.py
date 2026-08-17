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
from lib.uniserdes import Uniserdes, UniserdesField

class KnownDeviceConfig(Uniserdes):
    """Class that represents a single known device's information, which can be
    provided at runtime via a config file.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("name",             [str],      required=True),
            UniserdesField("macaddrs",         [list],     required=True),
            UniserdesField("tags",             [list],     required=False, default=[]),
        ]

    def post_parse_init(self):
        # sanitize MAC addresses
        self.macaddrs = [
            mac.strip().lower().replace("-", ":").replace(".", ":")
            for mac in self.macaddrs
        ]

        # sanitize tags
        self.tags = [tag.strip().lower() for tag in self.tags]

class DeviceHardwareAddress(Uniserdes):
    """Class that represents a device's unique hardware address."""
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("macaddr",          [str],      required=True),
            UniserdesField("vendor",           [str],      required=False, default=None),
        ]

class DeviceNetworkAddress(Uniserdes):
    """Class that represents a device's network connection address."""
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("ipaddr",           [str],      required=True),
        ]

class DevicePort(Uniserdes):
    """Class that represents a single open network port discovered on a device.

    Designed to be extensible: additional fields (e.g. product/version banners)
    can be added later without breaking serialization of existing records, since
    all fields except `port` are optional with defaults.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("port",             [int],      required=True),
            UniserdesField("protocol",         [str],      required=False, default="tcp"),
            UniserdesField("state",            [str],      required=False, default="open"),
            UniserdesField("service",          [str],      required=False, default=None),
            UniserdesField("scanned_at",       [datetime], required=False, default=None),
        ]

class DeviceOSInfo(Uniserdes):
    """Class that represents operating-system detection information for a device.

    Designed to be extensible: additional fields can be added later without
    breaking serialization of existing records, since all fields are optional
    with defaults.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("name",             [str],      required=False, default=None),
            UniserdesField("accuracy",         [int],      required=False, default=None),
            UniserdesField("family",           [str],      required=False, default=None),
            UniserdesField("scanned_at",       [datetime], required=False, default=None),
        ]

class Device(Uniserdes):
    """Class that represents a device. It has a configuration (`DeviceConfig`) and
    can have a `DeviceAddress`.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            UniserdesField("known_device",     [KnownDeviceConfig],        required=False, default=None),
            UniserdesField("hw_addr",          [DeviceHardwareAddress],    required=False, default=None),
            UniserdesField("net_addr",         [DeviceNetworkAddress],     required=False, default=None),
            UniserdesField("last_seen",        [datetime],                 required=False, default=None),
            UniserdesField("open_ports",       [DevicePort],               required=False, default=[]),
            UniserdesField("os_info",          [DeviceOSInfo],             required=False, default=None),
        ]

    def set_known_device(self, known_dev: KnownDeviceConfig):
        """Assigns a known device configuration to this device."""
        self.known_device = known_dev

    def set_hardware_address(self, hw_addr: DeviceHardwareAddress):
        """Sets the device's hardware address."""
        self.hw_addr = hw_addr

    def set_network_address(self, net_addr: DeviceNetworkAddress):
        """Sets the device's network address."""
        self.net_addr = net_addr

    def set_last_seen(self, timestamp: datetime):
        """Sets the time the device was last seen on the network."""
        self.last_seen = timestamp

    def set_open_ports(self, open_ports: list):
        """Sets the list of open ports (`DevicePort` objects) for this device."""
        self.open_ports = open_ports if open_ports is not None else []

    def set_os_info(self, os_info: DeviceOSInfo):
        """Sets the OS-detection information (`DeviceOSInfo`) for this device."""
        self.os_info = os_info

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
        result = "%s | MAC: %s | IP: %s | Last Seen: %s" % (name, macaddr, ipaddr, last_seen_str)

        # note the presence of open ports and OS info, if available
        if self.open_ports:
            result += " | Ports: %d" % len(self.open_ports)
        if self.os_info and self.os_info.name:
            result += " | OS: %s" % self.os_info.name
        return result

