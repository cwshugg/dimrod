# This module implements a stdlib-only wrapper around the command-line
# `arpspoof` tool (shipped as part of the `dsniff` package). It exposes an
# `ArpSpoof` class whose `poison()` method runs a bounded ARP cache-poisoning
# session against a single target host, tricking that host (and the gateway)
# into sending the target's traffic to this machine instead of directly to one
# another. Combined with disabling IP forwarding on the poisoning host, this
# lets the caller surgically cut an untrusted device off the local network.
#
# The invocation and privilege conventions mirror the sibling wrappers in
# `services/lib/arp.py` and `services/lib/nmap.py`:
#   * A configurable binary name resolved via `shutil.which`.
#   * All subprocess calls use an argument LIST (never `shell=True`), so
#     untrusted address/interface arguments can never be interpolated into a
#     shell string.
#   * Privileged operation is gated by reusing `lib.nmap.is_root` (arpspoof
#     needs raw-socket access / root) rather than re-implementing the check.
#
# SECURITY: even though an argument list is used, every caller-supplied value is
# strictly validated/allowlisted before being placed on the command line
# (target/gateway must be valid IPv4 strings; the interface must match a safe
# character set). This is defense-in-depth against argument-injection.
#
# The `arpspoof` process is ALWAYS bounded: `poison()` starts the process, waits
# up to `duration` seconds, then terminates it (SIGTERM, escalating to SIGKILL).
# Terminating arpspoof causes it to re-broadcast the correct ARP mappings on
# shutdown, restoring normal connectivity. `duration` is a HARD backstop; the
# process is never allowed to run unbounded.
#
#   Connor Shugg (Byteboy)

# Imports
import os
import sys
import re
import time
import shutil
import ipaddress
import subprocess

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Reuse the privilege helper from the nmap wrapper rather than duplicating the
# `os.geteuid()` logic (both wrappers need the exact same root check).
from lib.nmap import is_root


# ================================ Constants ================================= #
# Name of the underlying command-line binary this module wraps.
ARPSPOOF_BINARY = "arpspoof"

# How often (seconds) `poison()` wakes up while waiting out the requested
# duration. A short-ish poll keeps the wall-clock stop responsive without
# busy-spinning.
ARPSPOOF_DEFAULT_POLL = 1.0

# How long (seconds) to wait for a graceful SIGTERM shutdown (during which
# arpspoof restores the ARP tables) before escalating to SIGKILL.
ARPSPOOF_TERM_GRACE = 5.0

# Allowlist pattern for a network interface name. Linux interface names are
# short and restricted; this rejects anything that could smuggle in extra
# arguments or shell metacharacters even though we never use a shell.
ARPSPOOF_IFACE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")

# Upper bound on an interface name length (Linux `IFNAMSIZ` is 16, including the
# trailing NUL); kept as a defensive sanity cap.
ARPSPOOF_IFACE_MAX_LEN = 15


# ================================= Errors ================================== #
class ArpSpoofError(Exception):
    """Base error for the `arpspoof` wrapper.

    Raised when arpspoof fails to execute or is given invalid input.
    """
    pass


class ArpSpoofNotInstalledError(ArpSpoofError):
    """Raised when the `arpspoof` binary is not installed / not on `PATH`.

    This is the signal warden uses to soft-fail (refuse) ARP-poison requests
    with a clear message instead of crashing.
    """
    pass


class ArpSpoofPrivilegeError(ArpSpoofError):
    """Raised when ARP poisoning is attempted without root privileges.

    `arpspoof` requires raw-socket access, which requires root / `CAP_NET_RAW`.
    """
    pass


# ================================ ArpSpoof ================================= #
class ArpSpoof:
    """A light wrapper around the command-line `arpspoof` tool.

    A single instance can be reused for many `poison()` calls. The wrapper owns
    no long-lived state: each `poison()` spawns, bounds, and tears down its own
    subprocess.
    """

    def __init__(self, binary=ARPSPOOF_BINARY):
        """Constructor.

        Arguments:
          binary  Name (or path) of the `arpspoof` executable. Defaults to
                  `arpspoof`.
        """
        self.binary = binary

    # ---------------------------- Install Check ----------------------------- #
    @staticmethod
    def is_installed() -> bool:
        """Returns True if the `arpspoof` binary is available on the `PATH`.

        This is the install-enforcement primitive: callers use it to refuse
        ARP-poison requests (soft-fail) when arpspoof is not present, rather
        than discovering the failure only after a job has been enqueued.
        """
        return shutil.which(ARPSPOOF_BINARY) is not None

    # ------------------------------ Validation ------------------------------ #
    @staticmethod
    def _validate_ipv4(value, name: str) -> str:
        """Validates that `value` is a well-formed IPv4 address string.

        Returns the normalized (stripped) address. This both enforces
        correctness and prevents argument-injection: only strings that parse as
        a legal IPv4 address are ever placed on the command line.

        Arguments:
          value  The candidate address.
          name   A human-readable field name used in error messages.

        Raises:
          ArpSpoofError  If `value` is empty or not a valid IPv4 address.
        """
        if not isinstance(value, str) or len(value.strip()) == 0:
            raise ArpSpoofError("a non-empty %s IPv4 address is required" % name)
        value = value.strip()
        try:
            ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError as e:
            raise ArpSpoofError("invalid %s IPv4 address '%s': %s"
                                % (name, value, e)) from e
        return value

    @staticmethod
    def _validate_iface(iface) -> str:
        """Validates a network interface name against a strict allowlist.

        Returns the normalized (stripped) interface name.

        Arguments:
          iface  The candidate interface name (e.g. "eth0", "wlan0").

        Raises:
          ArpSpoofError  If `iface` is empty, too long, or contains characters
                         outside the safe `[A-Za-z0-9._:-]` set.
        """
        if not isinstance(iface, str) or len(iface.strip()) == 0:
            raise ArpSpoofError("a non-empty interface name is required")
        iface = iface.strip()
        if len(iface) > ARPSPOOF_IFACE_MAX_LEN:
            raise ArpSpoofError("interface name '%s' is too long" % iface)
        if ARPSPOOF_IFACE_PATTERN.match(iface) is None:
            raise ArpSpoofError("invalid interface name '%s' (allowed: "
                                "letters, digits, and '.', '_', ':', '-')"
                                % iface)
        return iface

    @staticmethod
    def _validate_duration(duration) -> int:
        """Validates and normalizes the requested duration into a positive int.

        Arguments:
          duration  The requested duration in seconds.

        Raises:
          ArpSpoofError  If `duration` is not a positive number.
        """
        try:
            duration = int(duration)
        except (TypeError, ValueError) as e:
            raise ArpSpoofError("duration must be an integer number of seconds") \
                from e
        if duration <= 0:
            raise ArpSpoofError("duration must be greater than 0 seconds")
        return duration

    # ------------------------------ Internal -------------------------------- #
    def _require_binary(self):
        """Verifies that the `arpspoof` binary is available on the system.

        Returns the resolved path to the binary.

        Raises:
          ArpSpoofNotInstalledError  If the binary cannot be found on `PATH`.
        """
        path = shutil.which(self.binary)
        if path is None:
            raise ArpSpoofNotInstalledError(
                "the '%s' binary was not found on PATH; install the 'dsniff' "
                "package to enable ARP-poison blocking" % self.binary)
        return path

    @staticmethod
    def _terminate(proc) -> bool:
        """Stops a running `arpspoof` process, giving it a chance to restore ARP.

        Sends SIGTERM first (arpspoof re-broadcasts the correct ARP mappings on
        a clean shutdown), waits a short grace period, then escalates to
        SIGKILL if the process is still alive.

        Arguments:
          proc  The `subprocess.Popen` handle to stop.

        Returns:
          True if the process was signalled to terminate (i.e. it was still
          running), False if it had already exited.
        """
        if proc.poll() is not None:
            return False

        # graceful stop: arpspoof restores the ARP tables on SIGTERM
        proc.terminate()
        try:
            proc.wait(timeout=ARPSPOOF_TERM_GRACE)
        except subprocess.TimeoutExpired:
            # hard stop backstop: the process ignored SIGTERM
            proc.kill()
            try:
                proc.wait(timeout=ARPSPOOF_TERM_GRACE)
            except subprocess.TimeoutExpired:
                pass
        return True

    # ------------------------------- Poison --------------------------------- #
    def poison(self, target_ip: str, gateway_ip: str, iface: str,
               duration, poll=ARPSPOOF_DEFAULT_POLL) -> dict:
        """Runs a bounded ARP cache-poisoning session against a single host.

        Executes:

            arpspoof -i <iface> -r -t <target_ip> <gateway_ip>

        as a subprocess for up to `duration` seconds, then terminates it so
        arpspoof restores the ARP tables on shutdown. The `-r` flag poisons
        both directions (target->gateway and gateway->target). `duration` is a
        HARD backstop: the process is NEVER allowed to run unbounded.

        NOTE: this only redirects the target's traffic to this machine. To
        actually black-hole (drop) the target, the caller must ALSO disable IP
        forwarding on this host for the duration; otherwise the traffic is
        transparently relayed. See warden's `_job_arppoison`.

        Arguments:
          target_ip   The IPv4 address of the host to cut off.
          gateway_ip  The IPv4 address of the LAN gateway/router.
          iface       The local network interface to poison over (e.g. "eth0").
          duration    Maximum number of seconds to poison for (hard backstop).
          poll        How often (seconds) to wake while waiting out `duration`.

        Returns:
          A dict summarizing the run:
            {
              "target":          <target ipv4>,
              "gateway":         <gateway ipv4>,
              "iface":           <interface name>,
              "duration":        <requested duration, seconds>,
              "elapsed_seconds": <wall-clock seconds the process ran>,
              "ran":             <bool: the process was started>,
              "terminated":      <bool: we had to stop it at the deadline>,
              "exited_early":    <bool: arpspoof exited on its own first>,
            }

        Raises:
          ArpSpoofNotInstalledError  If arpspoof is not installed.
          ArpSpoofPrivilegeError     If not running as root.
          ArpSpoofError              On invalid input or a failure to launch.
        """
        # fail fast on missing binary / insufficient privilege BEFORE touching
        # any caller input, so the errors are unambiguous.
        self._require_binary()
        if not is_root():
            raise ArpSpoofPrivilegeError(
                "ARP poisoning requires root privileges")

        # strictly validate/allowlist every value that reaches the command line
        target_ip = self._validate_ipv4(target_ip, "target")
        gateway_ip = self._validate_ipv4(gateway_ip, "gateway")
        iface = self._validate_iface(iface)
        duration = self._validate_duration(duration)

        # keep the poll interval sane (never longer than the whole duration,
        # never non-positive)
        try:
            poll = float(poll)
        except (TypeError, ValueError):
            poll = ARPSPOOF_DEFAULT_POLL
        poll = max(0.05, min(poll, float(duration)))

        # build the argument LIST (never a shell string)
        args = [self.binary, "-i", iface, "-r", "-t", target_ip, gateway_ip]

        # spawn the process; discard its (noisy) output
        try:
            proc = subprocess.Popen(args,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ArpSpoofError("failed to execute '%s': %s"
                                % (self.binary, e)) from e

        start = time.monotonic()
        deadline = start + duration
        exited_early = False
        try:
            # wait out the duration, waking every `poll` seconds. If arpspoof
            # exits on its own before the deadline, stop waiting.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    proc.wait(timeout=min(poll, remaining))
                    exited_early = True
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            # ALWAYS ensure the process is stopped (ARP restored) on the way out
            terminated = self._terminate(proc)

        elapsed = time.monotonic() - start
        return {
            "target": target_ip,
            "gateway": gateway_ip,
            "iface": iface,
            "duration": duration,
            "elapsed_seconds": elapsed,
            "ran": True,
            "terminated": terminated,
            "exited_early": exited_early,
        }
