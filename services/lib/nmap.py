# This module implements a stdlib-only wrapper around the command-line `nmap`
# network scanner. It exposes an `Nmap` class providing host discovery
# (`ping_host`, `scan_range`), TCP port scanning (`scan_ports`), and OS
# detection (`detect_os`), plus a module-level `is_root()` helper.
#
# The invocation patterns mirror the proven `ping_nmap`/`sweep` logic in
# `services/warden/warden.py` (e.g. `nmap -sn -P<E/P/M/S/A/U>` for host
# discovery, `--max-rtt-timeout` limits), but with two deliberate improvements:
#   * Output is parsed from nmap's XML output (`-oX -`) via
#     `xml.etree.ElementTree`, streamed over stdout, instead of a fixed-name
#     temp file (`.warden.nmap.out`) that could collide between concurrent
#     scans. Greppable stdout (`-oG -`) is used only for the simple up/down
#     `ping_host` check.
#   * Privileged operations (`-sS` SYN scan, `-O` OS detection) require root;
#     when the process is unprivileged these degrade gracefully with a clear
#     error rather than silently failing.
#
# All subprocess calls use argument lists (never `shell=True`), so untrusted
# address/range arguments are never interpolated into a shell string.
#
#   Connor Shugg

# Imports
import os
import sys
import shutil
import subprocess
import xml.etree.ElementTree as ET

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)


# ================================ Constants ================================= #
# Name of the underlying command-line binary this module wraps.
NMAP_BINARY = "nmap"

# Default timeout (seconds) passed to nmap's `--max-rtt-timeout` for pings and
# used to bound how long the subprocess itself is allowed to run.
NMAP_DEFAULT_TIMEOUT = 2.0
# Default number of ping attempts, mirroring warden's retry behavior.
NMAP_DEFAULT_TRIES = 2

# Mapping of short ping-type identifiers to their nmap host-discovery flags.
# Mirrors the set supported by warden's `ping_nmap`.
NMAP_PING_TYPES = {
    "pe": "-PE",    # ICMP echo request (default; equivalent to `ping`)
    "pp": "-PP",    # ICMP timestamp query
    "pm": "-PM",    # ICMP address-mask query
    "ps": "-PS",    # TCP SYN ping
    "pa": "-PA",    # TCP ACK ping
    "pu": "-PU",    # UDP ping
}
NMAP_DEFAULT_PING_TYPE = "pe"


# ============================= Privilege Helper ============================= #
def is_root() -> bool:
    """Returns True if the current process has root/superuser privileges.

    Uses `os.geteuid()`, which is unavailable on some platforms (notably
    Windows); on such platforms this conservatively returns False, causing
    privileged nmap features to degrade gracefully.
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return geteuid() == 0


# ================================= Errors ================================== #
class NmapError(Exception):
    """Raised when nmap is missing, fails to execute, or produces bad output."""
    pass


class NmapPrivilegeError(NmapError):
    """Raised when a privileged nmap operation is attempted without root.

    Applies to SYN scans (`-sS`) and OS detection (`-O`), both of which require
    root / `CAP_NET_RAW`.
    """
    pass


# ================================== Nmap =================================== #
class Nmap:
    """A light wrapper around the command-line `nmap` scanner.

    Each operation returns structured Python data (bools, lists of dicts, or
    dicts) and annotates nmap failures via `NmapError`. Privileged operations
    raise `NmapPrivilegeError` when run unprivileged.
    """

    def __init__(self, binary=NMAP_BINARY, timeout=NMAP_DEFAULT_TIMEOUT):
        """Constructor.

        Arguments:
          binary   Name (or path) of the `nmap` executable. Defaults to `nmap`.
          timeout  Default per-scan timeout in seconds.
        """
        self.binary = binary
        self.timeout = timeout

    # ---------------------------- Internal Helpers ---------------------------- #
    def _require_binary(self):
        """Verifies the `nmap` binary is available, raising `NmapError` if not.

        Returns the resolved path to the binary.
        """
        path = shutil.which(self.binary)
        if path is None:
            raise NmapError("the '%s' binary was not found on PATH" % self.binary)
        return path

    def _normalize_target(self, target: str) -> str:
        """Validates and normalizes a target address/range/CIDR argument.

        This does not fully validate the target as a legal IP/CIDR (nmap itself
        does that); it simply rejects obviously unsafe or empty values so that
        untrusted API-supplied strings can't smuggle in extra arguments.

        Arguments:
          target  The address, hostname, range, or CIDR to scan.

        Raises:
          NmapError  If the target is empty or looks like an nmap option/flag.
        """
        if not isinstance(target, str) or len(target.strip()) == 0:
            raise NmapError("a non-empty target string is required")
        target = target.strip()
        # reject values that would be interpreted by nmap as an option flag
        if target.startswith("-"):
            raise NmapError("invalid target '%s' (must not start with '-')"
                            % target)
        # reject embedded whitespace, which would split into multiple args
        if any(c.isspace() for c in target):
            raise NmapError("invalid target '%s' (must not contain whitespace)"
                            % target)
        return target

    def _run(self, args: list):
        """Runs nmap with the given argument list and returns its stdout string.

        The binary name is prepended automatically. Uses an argument list (no
        shell) and enforces the configured timeout.

        Arguments:
          args  The nmap arguments (excluding the binary name itself).

        Raises:
          NmapError  If the process cannot be launched, times out, or exits
                     with a non-zero status.
        """
        self._require_binary()
        full_args = [self.binary] + args
        try:
            result = subprocess.run(full_args,
                                    capture_output=True,
                                    timeout=self._process_timeout())
        except subprocess.TimeoutExpired:
            raise NmapError("nmap timed out after %s seconds"
                            % self._process_timeout())
        except OSError as e:
            raise NmapError("failed to execute '%s': %s" % (self.binary, e))

        # a non-zero exit code indicates nmap failed; surface its stderr
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise NmapError("nmap exited with code %d: %s"
                            % (result.returncode, stderr))
        return result.stdout.decode(errors="replace")

    def _process_timeout(self):
        """Computes the wall-clock timeout for the nmap subprocess itself.

        Kept generously larger than the per-probe `--max-rtt-timeout` so that
        multi-host or port scans have room to finish before we forcibly abort.
        """
        return max(self.timeout, 1.0) * 30.0

    @staticmethod
    def _parse_xml(xml_text: str):
        """Parses nmap XML output text into an ElementTree root element.

        Arguments:
          xml_text  The XML string emitted by `nmap -oX -`.

        Raises:
          NmapError  If the output is empty or not well-formed XML.
        """
        if xml_text is None or len(xml_text.strip()) == 0:
            raise NmapError("nmap produced no XML output")
        try:
            return ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise NmapError("failed to parse nmap XML output: %s" % e)

    # ------------------------------ Host Discovery ---------------------------- #
    def ping_host(self, address: str, timeout=None, tries=None,
                  pingtype=None) -> bool:
        """Determines whether a single host is up via an nmap ping scan.

        Runs `nmap -sn -P<...>` and parses greppable stdout for the host's
        "Up" status, retrying up to `tries` times (mirroring warden's
        `ping_nmap`). Returns True if the host responded, False otherwise.

        Arguments:
          address   The host address to probe.
          timeout   Per-probe RTT timeout in seconds (defaults to `self.timeout`).
          tries     Number of attempts before giving up.
          pingtype  A key from `NMAP_PING_TYPES` selecting the probe technique.

        Raises:
          NmapError  If nmap is missing, errors out, or the ping type is invalid.
        """
        address = self._normalize_target(address)
        base_timeout = self.timeout if timeout is None else timeout
        tries = NMAP_DEFAULT_TRIES if tries is None else tries

        # select the ping-type flag, defaulting to an ICMP echo request
        key = NMAP_DEFAULT_PING_TYPE if pingtype is None else pingtype.strip().lower()
        if key not in NMAP_PING_TYPES:
            raise NmapError("unknown ping type '%s'" % key)
        ptarg = NMAP_PING_TYPES[key]

        # attempt the ping a bounded number of times, widening the timeout
        for i in range(max(tries, 1)):
            probe_timeout = base_timeout + (i * base_timeout)
            args = [
                "-sn",                                  # ping scan only
                ptarg,                                  # selected ping technique
                "--max-rtt-timeout", "%ss" % probe_timeout,
                "-oG", "-",                             # greppable output to stdout
                address,
            ]
            stdout = self._run(args)

            # look for a line naming this host and reporting it as "up"
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("#"):
                    continue
                if address in line and "up" in line.lower():
                    return True

        return False

    def scan_range(self, target: str, timeout=None) -> list:
        """Performs a host-discovery sweep over an IP range or CIDR.

        Runs `nmap -sn <target>` with XML output and returns a list of dicts
        describing each host that is up. Each dict contains:
          {
            "address": <ipv4 string>,
            "macaddr": <mac string or None>,
            "vendor":  <vendor string or None>,
          }

        Arguments:
          target   An IP range (e.g. "10.0.0.1-20") or CIDR (e.g. "10.0.0.0/24").
          timeout  Per-probe RTT timeout in seconds (defaults to `self.timeout`).

        Raises:
          NmapError  If nmap is missing, errors out, or emits bad XML.
        """
        target = self._normalize_target(target)
        probe_timeout = self.timeout if timeout is None else timeout
        args = [
            "-sn",                                      # ping scan only
            "--max-rtt-timeout", "%ss" % probe_timeout,
            "-oX", "-",                                 # XML output to stdout
            target,
        ]
        stdout = self._run(args)
        root = self._parse_xml(stdout)

        hosts = []
        for host in root.findall("host"):
            # only include hosts nmap reports as up
            status = host.find("status")
            if status is None or status.get("state") != "up":
                continue

            info = {"address": None, "macaddr": None, "vendor": None}
            # a host may report multiple <address> elements (ipv4, mac, ...)
            for addr in host.findall("address"):
                addrtype = addr.get("addrtype")
                if addrtype in ("ipv4", "ipv6"):
                    info["address"] = addr.get("addr")
                elif addrtype == "mac":
                    info["macaddr"] = addr.get("addr")
                    info["vendor"] = addr.get("vendor")

            if info["address"] is not None:
                hosts.append(info)

        return hosts

    # ------------------------------- Port Scan -------------------------------- #
    def scan_ports(self, address: str, ports=None, timeout=None) -> list:
        """Performs a TCP port scan against a single host.

        Uses a SYN scan (`-sS`) when running as root, otherwise falls back to a
        TCP connect scan (`-sT`) so unprivileged callers still get results.
        Returns a list of dicts, one per reported port:
          {
            "port": <int>,
            "protocol": <str>,      # e.g. "tcp"
            "state": <str>,         # e.g. "open", "closed", "filtered"
            "service": <str|None>,  # e.g. "ssh", if nmap identified one
          }

        Arguments:
          address  The single host address to scan.
          ports    Optional port spec string (e.g. "22,80,443" or "1-1024").
                   When None, nmap's default port set is used.
          timeout  Per-probe RTT timeout in seconds (defaults to `self.timeout`).

        Raises:
          NmapError  If nmap is missing, errors out, or emits bad XML.
        """
        address = self._normalize_target(address)
        probe_timeout = self.timeout if timeout is None else timeout

        # choose the scan technique based on privilege: SYN needs root
        scan_flag = "-sS" if is_root() else "-sT"
        args = [
            scan_flag,
            "--max-rtt-timeout", "%ss" % probe_timeout,
            "-oX", "-",                                 # XML output to stdout
        ]
        # add an explicit port spec if one was requested
        if ports is not None:
            args += ["-p", self._normalize_ports(ports)]
        args.append(address)

        stdout = self._run(args)
        root = self._parse_xml(stdout)

        results = []
        for host in root.findall("host"):
            ports_el = host.find("ports")
            if ports_el is None:
                continue
            for port in ports_el.findall("port"):
                state_el = port.find("state")
                service_el = port.find("service")
                results.append({
                    "port": int(port.get("portid")),
                    "protocol": port.get("protocol"),
                    "state": state_el.get("state") if state_el is not None else None,
                    "service": service_el.get("name") if service_el is not None else None,
                })

        return results

    @staticmethod
    def _normalize_ports(ports) -> str:
        """Validates and normalizes a port specification into a safe string.

        Accepts either an int, a list/tuple of ints, or a string spec such as
        "22,80,443" or "1-1024". Rejects characters outside the digit / comma /
        dash set so the value can't smuggle in extra arguments.

        Raises:
          NmapError  If the spec is empty or contains disallowed characters.
        """
        # accept a single int or a collection of ints for convenience
        if isinstance(ports, int):
            ports = str(ports)
        elif isinstance(ports, (list, tuple)):
            ports = ",".join(str(int(p)) for p in ports)

        if not isinstance(ports, str) or len(ports.strip()) == 0:
            raise NmapError("a non-empty port specification is required")
        ports = ports.strip()

        # only digits, commas, and dashes are valid in an nmap port spec
        allowed = set("0123456789,-")
        if not set(ports).issubset(allowed):
            raise NmapError("invalid port specification '%s'" % ports)
        return ports

    # ------------------------------ OS Detection ------------------------------ #
    def detect_os(self, address: str, timeout=None) -> dict:
        """Performs OS detection against a single host (requires root).

        Runs `nmap -O` and parses the XML `<osmatch>` entries. Returns a dict:
          {
            "address": <ipv4 string or None>,
            "matches": [ {"name": <str>, "accuracy": <int>}, ... ],
          }
        sorted by descending accuracy.

        Arguments:
          address  The single host address to fingerprint.
          timeout  Per-probe RTT timeout in seconds (defaults to `self.timeout`).

        Raises:
          NmapPrivilegeError  If invoked without root privileges.
          NmapError           If nmap is missing, errors out, or emits bad XML.
        """
        # OS detection requires raw sockets, which require root
        if not is_root():
            raise NmapPrivilegeError("OS detection requires root privileges")

        address = self._normalize_target(address)
        probe_timeout = self.timeout if timeout is None else timeout
        args = [
            "-O",                                       # enable OS detection
            "--max-rtt-timeout", "%ss" % probe_timeout,
            "-oX", "-",                                 # XML output to stdout
            address,
        ]
        stdout = self._run(args)
        root = self._parse_xml(stdout)

        result = {"address": address, "matches": []}
        host = root.find("host")
        if host is None:
            return result

        # record the resolved address if nmap reported one
        for addr in host.findall("address"):
            if addr.get("addrtype") in ("ipv4", "ipv6"):
                result["address"] = addr.get("addr")
                break

        # collect every OS match, capturing name and accuracy
        os_el = host.find("os")
        if os_el is not None:
            for match in os_el.findall("osmatch"):
                accuracy = match.get("accuracy")
                result["matches"].append({
                    "name": match.get("name"),
                    "accuracy": int(accuracy) if accuracy is not None else None,
                })

        # sort the best (highest-accuracy) matches first
        result["matches"].sort(
            key=lambda m: m["accuracy"] if m["accuracy"] is not None else -1,
            reverse=True,
        )
        return result
