# This module provides a basic wrapper around the unofficial Wyze Python SDK.
#
#   https://wyze-sdk.readthedocs.io/en/latest/wyze_sdk.api.html
#   https://pypi.org/project/wyze-sdk/

# Imports
import os
import sys
import logging
import time

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField

# Wyze imports
import wyze_sdk
from wyze_sdk import Client

# A configuration object for creating a Wyze client.
class WyzeConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("email",            [str],      required=True),
            ConfigField("password",         [str],      required=True),
            ConfigField("api_key_id",       [str],      required=True),
            ConfigField("api_key",          [str],      required=True),
            ConfigField("retry_attempts",   [int],      required=False, default=4),
            ConfigField("retry_delay",      [int],      required=False, default=1)
        ]

# A class used to authenticate with the Wyze API via the Wyze SDK.
class Wyze:
    # Constructor.
    def __init__(self, config: WyzeConfig, debug_log=False):
        self.config = config
        self.client = None
        if debug_log:
            wyze_sdk.set_stream_logger("wyze_sdk", level=logging.DEBUG)
    
    # Helper function for asserting that the current object is not logged in.
    def assert_not_authenticated(self):
        assert self.client is None, "You have already logged in to Wyze."
    
    # Helper function for asserting that the current object is not logged in.
    def assert_is_authenticated(self):
        assert self.client is not None, "You have not logged in to Wyze."

    # Attempts to authenticate with the Wyze API using the config object that
    # was given during `__init__()`.
    def login(self):
        self.assert_not_authenticated()
        self.client = Client()
        
        err = None
        for i in range(self.config.retry_attempts):
            try:
                return self.client.login(email=self.config.email,
                                         password=self.config.password,
                                         key_id=self.config.api_key_id,
                                         api_key=self.config.api_key)
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        raise err
    
    # Refreshes the internal client after already logging in.
    def refresh(self):
        # attempt to log out (we don't care if this fails)
        try:
            self.client.logout()
        except:
            pass
        
        # reset the client and create a new one
        self.client = None
        self.login()

    # --------------------------- Device Retrieval --------------------------- #
    # Retrieves and returns a list of all devices on your account.
    def get_devices(self):
        self.assert_is_authenticated()

        err = None
        for i in range(self.config.retry_attempts):
            try:
                return self.client.devices_list()
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        raise err

    # Given a MAC address, this retrieves information on a specific plug.
    def get_plug(self, macaddr: str):
        self.assert_is_authenticated()
        
        err = None
        for i in range(self.config.retry_attempts):
            try:
                return self.client.plugs.info(device_mac=macaddr)
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        raise err

    
    # --------------------------- Device Toggling ---------------------------- #
    # Helper function for toggling switches on and off. If `power_on` is True,
    # the switch will be turned on. Otherwise, it will be turned off.
    def toggle_plug(self, macaddr: str, power_on: bool):
        self.assert_is_authenticated()
        plug = self.get_plug(macaddr)
        assert plug is not None, "Cannot find plug with MAC address \"%s\"" % macaddr

        err = None
        for i in range(self.config.retry_attempts):
            try:
                # turn on or off, depending on what option was passed in
                if power_on:
                    return self.client.plugs.turn_on(device_mac=plug.mac,
                                                     device_model=plug.product.model)
                return self.client.plugs.turn_off(device_mac=plug.mac,
                                                  device_model=plug.product.model)
            except Exception as e:
                err = e
                time.sleep(self.config.retry_delay)
        raise err

