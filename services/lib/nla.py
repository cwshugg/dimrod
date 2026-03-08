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
from lib.uniserdes import Uniserdes, UniserdesField
from lib.oracle import Oracle, OracleSessionConfig

# A specific function definition that represents the handler function
# for a single NLA endpoint.
NLAEndpointHandlerFunction = Callable[[Oracle, dict], dict]

# Defines an object used to represent a single service that supports one or
# more NLA endpoints.
class NLAService(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name",         [str],      required=True),
            UniserdesField("oracle", [OracleSessionConfig], required=True),
        ]

# Defines information regarding a specific NLA HTTP endpoint.
class NLAEndpoint(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("name",         [str],      required=True),
            UniserdesField("description",  [str],      required=True),
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
class NLAEndpointInvokeParameters(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("message",      [str],      required=True),
            UniserdesField("substring",    [str],      required=False, default=None),
            UniserdesField("extra_params", [dict],     required=False, default=None),
        ]

    def has_substring(self):
        return hasattr(self, "substring") and \
               self.substring is not None and \
               len(str(self.substring).strip()) > 0

# Defines the result of an NLA invocation.
class NLAResult(Uniserdes):
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("success",      [bool],     required=True),
            UniserdesField("message",      [str],      required=False, default=None),
            UniserdesField("message_context", [str],   required=False, default=None),
            UniserdesField("payload",      [dict],     required=False, default=None),
        ]

