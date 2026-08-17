# This module implements a minimal, stdlib-only wrapper around the command-line
# `arp` tool. It exposes an `Arp` class whose `lookup()` method resolves an IP
# address to its MAC address by parsing the output of `arp -n <ip>`.
#
# The invocation and parsing logic mirrors the proven `arp` method in
# `services/warden/warden.py`: it runs `arp -n <ip>` with an argument list (no
# shell), then scans the table for the row whose first column equals the target
# IP and returns that row's MAC address (third column).
#
#   Connor Shugg

# Imports
import os
import sys
import shutil
import subprocess

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)


# ================================ Constants ================================= #
# Name of the underlying command-line binary this module wraps.
ARP_BINARY = "arp"
# Substring `arp` prints (in a row) when it has no entry for the given IP.
ARP_NO_ENTRY_MARKER = "no entry"


# ================================= Errors ================================== #
class ArpError(Exception):
    """Raised when the `arp` binary is missing or fails to execute.

    Note that a *successful* lookup with no matching entry is not an error; in
    that case `Arp.lookup()` returns `None`.
    """
    pass


# ================================== Arp ==================================== #
class Arp:
    """A light wrapper around the command-line `arp` tool.

    The wrapper is intentionally minimal: it only resolves IP addresses to MAC
    addresses via the system ARP cache. It never performs network I/O itself;
    populating the ARP cache (e.g. by pinging first) is the caller's job.
    """

    def __init__(self, binary=ARP_BINARY):
        """Constructor.

        Arguments:
          binary  Name (or path) of the `arp` executable. Defaults to `arp`.
        """
        self.binary = binary

    def _require_binary(self):
        """Verifies that the `arp` binary is available on the system.

        Returns the resolved path to the binary, or raises `ArpError` if it
        cannot be found on the `PATH`.
        """
        path = shutil.which(self.binary)
        if path is None:
            raise ArpError("the '%s' binary was not found on PATH" % self.binary)
        return path

    def lookup(self, ipaddr: str):
        """Looks up the MAC address associated with the given IP address.

        Runs `arp -n <ipaddr>` and parses the resulting table, returning the
        MAC address (as a string) for the row whose first column matches
        `ipaddr`. Returns `None` if the IP has no entry in the ARP cache.

        Arguments:
          ipaddr  The IPv4 address string to resolve.

        Raises:
          ArpError  If the `arp` binary is missing or the process errors out.
        """
        # normalize/validate input: reject empty or whitespace-only values
        if not isinstance(ipaddr, str) or len(ipaddr.strip()) == 0:
            raise ArpError("a non-empty IP address string is required")
        ipaddr = ipaddr.strip()

        # make sure the binary exists before attempting to run it
        self._require_binary()

        # invoke `arp -n <ip>` with an argument list (never shell=True)
        args = [self.binary, "-n", ipaddr]
        try:
            result = subprocess.run(args, capture_output=True)
        except OSError as e:
            raise ArpError("failed to execute '%s': %s" % (self.binary, e))

        # a non-empty stderr indicates the command reported a problem
        stderr = result.stderr.decode(errors="replace").strip()
        if len(stderr) > 0:
            raise ArpError("'%s' produced error messages: %s"
                           % (self.binary, stderr))

        # parse the table line by line, looking for the matching IP row
        stdout = result.stdout.decode(errors="replace")
        for line in stdout.split("\n"):
            line = line.strip()
            # skip blank lines and "no entry" notices
            if len(line) == 0 or ARP_NO_ENTRY_MARKER in line.lower():
                continue

            # the first column is the address; when it matches, the third
            # column holds the hardware (MAC) address
            pieces = line.split()
            if len(pieces) >= 3 and pieces[0] == ipaddr:
                return pieces[2]

        # no matching entry was found
        return None
