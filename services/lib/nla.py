# NLA = "Natural Language Actions"
#
# This module defines objects and functions used by Oracles to:
#
# 1. Advertise the different NLA (Natural Language Action) a service supports.
# 2. Process NLA requests.

# Imports
import os
import sys
from typing import Callable

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.oracle import Oracle, OracleSessionConfig

# A specific function definition that represents the handler function
# for a single NLA endpoint.
NLAEndpointHandlerFunction = Callable[[Oracle, dict], dict]

# Defines an object used to represent a single service that supports one or
# more NLA endpoints.
class NLAService(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("oracle", [OracleSessionConfig], required=True),
        ]

# Defines information regarding a specific NLA HTTP endpoint.
class NLAEndpoint(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("name",         [str],      required=True),
            ConfigField("description",  [str],      required=True),
        ]

    # Returns the full URL to invoke this NLA endpoint.
    def get_url(self):
        return "/nla/invoke/" + self.name

    # Passes in a pointer to a function that will be invoked by the Oracle
    # endpoint corresponding to this NLAEndpoint.
    def set_handler(self, func: NLAEndpointHandlerFunction):
        self.handler = func
        return self

# Defines fields used to invoke an NLA endpoint.
class NLAEndpointInvokeParameters(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("message",      [str],      required=True),
            ConfigField("substring",    [str],      required=False, default=None),
        ]

# Defines the result of an NLA invocation.
class NLAResult(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("success",      [bool],     required=True),
            ConfigField("message",      [str],      required=False, default=None),
            ConfigField("message_context", [str],   required=False, default=None),
        ]

