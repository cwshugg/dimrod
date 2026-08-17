# Implements the /network bot command.

# Imports
import os
import sys
import html
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
from warden.device import Device


# ================================ Constants ================================= #
# Timestamp format shared with the "Last seen" line for consistency.
_TS_FMT = "%Y-%m-%d %I:%M:%S %p"


# ================================= Helpers ================================== #
def _esc(text) -> str:
    """HTML-escapes a value for safe inclusion in a Telegram HTML message."""
    if text is None:
        return ""
    return html.escape(str(text))


def get_warden_session(service, message):
    """Creates and authenticates an `OracleSession` with the warden service.

    Returns the session on success, or None on failure (after sending an
    appropriate error message to the user). Centralizes the Warden-offline and
    login-failure handling shared by every code path in this command.
    """
    session = OracleSession(service.config.warden)
    try:
        r = session.login()
    except Exception:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Warden. "
                             "It might be offline.")
        return None

    # check the login response
    if r.status_code != 200:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Warden.")
        return None
    if not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with Warden. "
                             "(%s)" % session.get_response_message(r))
        return None
    return session


def get_devices(service, message, session):
    """Retrieves and parses the list of devices from warden, sorted so the
    most-recently-seen device appears first.

    Returns a list of `Device` objects on success, or None on failure (after
    sending an error message to the user).
    """
    try:
        r = session.get("/devices")
        device_data = session.get_response_json(r)

        # parse each as a warden `Device` object
        devices = []
        for entry in device_data:
            devices.append(Device.from_json(entry))

        # sort by last-seen time (most recently-seen first)
        devices = list(reversed(sorted(
            devices, key=lambda d: d.last_seen.timestamp())))
        return devices
    except Exception as e:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't retrieve a list of devices from Warden. "
                             "(%s)" % e)
        return None


def network_list_times(service, message, args, devices):
    """Creates and sends a list of cached devices, sorted by last-seen time."""
    msg = "<b>All Cached Devices</b>\n\n"

    # sort the devices into buckets based on last-seen time
    buckets = [
        {"name": "Currently online",                        "time": 120,    "list": []},
        {"name": "Last seen 5 minutes ago",                 "time": 300,    "list": []},
        {"name": "Last seen 5-15 minutes ago",              "time": 900,    "list": []},
        {"name": "Last seen 15-30 minutes ago",             "time": 1800,   "list": []},
        {"name": "Last seen within the last hour",          "time": 3600,   "list": []},
        {"name": "Last seen within the last four hours",    "time": 14400,  "list": []},
        {"name": "Last seen within the last eight hours",   "time": 28800,  "list": []},
        {"name": "Last seen within the last day",           "time": 86400,  "list": []},
        {"name": "Last seen within the last two days",      "time": 172800,  "list": []},
        {"name": "Last seen within the last three days",    "time": 259200,  "list": []},
        {"name": "Last seen within the last four days",     "time": 345600,  "list": []},
        {"name": "Last seen within the last five days",     "time": 432000,  "list": []},
        {"name": "Last seen within the last six days",      "time": 518400,  "list": []},
        {"name": "Last seen within the last week",          "time": 604800, "list": []},
        {"name": "Last seen within the last two weeks",     "time": 1209600, "list": []},
        {"name": "Last seen within the last month",         "time": 2419200, "list": []},
        {"name": "Last seen within the last two months",    "time": 4838400, "list": []},
        {"name": "Last seen within the last four months",   "time": 9676800, "list": []},
        {"name": "Last seen within the last year",          "time": 29030400, "list": []}
    ]
    now = datetime.now()
    for device in devices:
        diff = now.timestamp() - device.last_seen.timestamp()
        for b in buckets:
            # if the time since last seen fits in the bucket's time
            # window, add it to the bucket
            if int(diff) <= int(b["time"]):
                b["list"].append(device)
                break

    # now, prepare a message listing off any non-empty buckets
    for b in buckets:
        if len(b["list"]) == 0:
            continue
        msg += "<b>%s:</b>\n" % b["name"]
        for device in b["list"]:
            # add the device's name or MAC address to the message
            device_is_known = device.known_device is not None
            if device_is_known:
                msg += "· <b><i>%s</i></b>" % _esc(device.known_device.name)
            else:
                msg += "· <code>%s</code>" % _esc(device.hw_addr.macaddr)
                if device.hw_addr.vendor is not None:
                    msg += " (<i>%s</i>)" % _esc(device.hw_addr.vendor)

            # add the last-seen time (if it's on the same day, don't
            # include the day in the date string)
            dtstr = device.last_seen.strftime("%I:%M:%S %p")
            if now.year != device.last_seen.year or \
                now.month != device.last_seen.month or \
                now.day != device.last_seen.day:
                dtstr = "%s at %s" % (device.last_seen.strftime("%Y-%m-%d"), dtstr)
            msg += " - %s" % dtstr

            # append nested sub-bullets for the device's IP and MAC. The IP is
            # shown (when present) for both known and unknown devices. The MAC
            # sub-bullet is only added for KNOWN devices, since unknown devices
            # already display their MAC on the main bullet above (avoids showing
            # the MAC twice). Empty values are never rendered.
            if device.net_addr is not None and \
                    device.net_addr.ipaddr is not None:
                msg += "\n    · IP: <code>%s</code>" % \
                    _esc(device.net_addr.ipaddr)
            if device_is_known and \
                    device.hw_addr is not None and \
                    device.hw_addr.macaddr is not None:
                msg += "\n    · MAC: <code>%s</code>" % \
                    _esc(device.hw_addr.macaddr)
                if device.hw_addr.vendor is not None:
                    msg += " (<i>%s</i>)" % _esc(device.hw_addr.vendor)

            # note any collected scan data (open ports / detected OS) compactly
            if device.os_info is not None and device.os_info.name:
                msg += "\n    · OS: <code>%s</code>" % _esc(device.os_info.name)
            if device.open_ports:
                msg += "\n    · %d open port%s" % \
                    (len(device.open_ports),
                     "" if len(device.open_ports) == 1 else "s")

            # append a final newline
            msg += "\n"
        msg += "\n"

    # send the message
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return True


# ---------------------------- Device Detail View ---------------------------- #
def _device_matches(device, query: str) -> bool:
    """Returns True if `query` identifies `device` by IP, MAC, or known name."""
    q = query.strip().lower()
    if len(q) == 0:
        return False

    # match on IP address
    if device.net_addr is not None and device.net_addr.ipaddr is not None:
        if device.net_addr.ipaddr.strip().lower() == q:
            return True

    # match on MAC address (normalize common separators to colons)
    if device.hw_addr is not None and device.hw_addr.macaddr is not None:
        mac = device.hw_addr.macaddr.strip().lower()
        qmac = q.replace("-", ":").replace(".", ":")
        if mac == qmac:
            return True

    # match on the known-device name (exact or substring, case-insensitive)
    if device.known_device is not None and device.known_device.name is not None:
        name = device.known_device.name.strip().lower()
        if q == name or q in name:
            return True
    return False


def _render_device_detail(device) -> str:
    """Builds a clean HTML detail block for a single device, including its open
    ports and detected OS when that data is present (omitted otherwise).
    """
    # header / name line
    if device.known_device is not None and device.known_device.name:
        header = _esc(device.known_device.name)
    else:
        header = "Unknown Device"
    msg = "🔎 <b>Device Detail: %s</b>\n\n" % header

    # MAC (+ vendor)
    if device.hw_addr is not None and device.hw_addr.macaddr is not None:
        msg += "<b>MAC:</b> <code>%s</code>" % _esc(device.hw_addr.macaddr)
        if device.hw_addr.vendor is not None:
            msg += " (<i>%s</i>)" % _esc(device.hw_addr.vendor)
        msg += "\n"

    # IP
    if device.net_addr is not None and device.net_addr.ipaddr is not None:
        msg += "<b>IP:</b> <code>%s</code>\n" % _esc(device.net_addr.ipaddr)

    # last seen
    if device.last_seen is not None:
        msg += "<b>Last seen:</b> %s\n" % \
            device.last_seen.strftime("%Y-%m-%d %I:%M:%S %p")

    # open ports (omit the section entirely when absent)
    if device.open_ports:
        # all ports from one scan share a `scanned_at`; surface the most
        # recent present value on the section header (omit if none present)
        scanned = [p.scanned_at for p in device.open_ports
                   if p.scanned_at is not None]
        if scanned:
            ts = max(scanned).strftime(_TS_FMT)
            msg += "\n<b>Open ports:</b> <i>(scanned %s)</i>\n" % _esc(ts)
        else:
            msg += "\n<b>Open ports:</b>\n"
        for p in device.open_ports:
            proto = p.protocol if p.protocol else "tcp"
            line = "· <code>%s/%s</code>" % (_esc(p.port), _esc(proto))
            if p.service:
                line += " — <i>%s</i>" % _esc(p.service)
            msg += line + "\n"

    # detected OS (omit when absent)
    if device.os_info is not None and device.os_info.name:
        msg += "\n<b>OS:</b> %s" % _esc(device.os_info.name)
        if device.os_info.accuracy is not None:
            msg += " (%s%% confidence)" % _esc(device.os_info.accuracy)
        if device.os_info.family:
            msg += " · <i>%s</i>" % _esc(device.os_info.family)
        if device.os_info.scanned_at is not None:
            ts = device.os_info.scanned_at.strftime(_TS_FMT)
            msg += " · <i>scanned %s</i>" % _esc(ts)
        msg += "\n"

    return msg


def _cmd_device_detail(service, message, session, devices, query: str) -> bool:
    """Finds the device identified by `query` and sends its detail block."""
    match = None
    for device in devices:
        if _device_matches(device, query):
            match = device
            break

    if match is None:
        service.send_message(message.chat.id,
                             "I couldn't find a cached device matching "
                             "<code>%s</code>. Try <code>/net</code> to list "
                             "all known devices." % _esc(query),
                             parse_mode="HTML")
        return False

    service.send_message(message.chat.id, _render_device_detail(match),
                         parse_mode="HTML")
    return True


# ------------------------------- Job Handling ------------------------------- #
def _submit_job(service, message, session, endpoint, payload):
    """Submits a warden job via POST and returns its job id.

    Returns the job id string on success. On failure, sends a graceful error
    message to the user (surfacing warden's refusal reason, e.g. arppoison
    guardrails) and returns None.
    """
    try:
        r = session.post(endpoint, payload=payload)
    except Exception:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach Warden. "
                             "It might be offline.")
        return None

    # surface warden's refusal message (e.g. off-subnet target, arpspoof not
    # installed) rather than a generic failure
    try:
        success = session.get_response_success(r)
    except Exception:
        success = False
    if getattr(r, "status_code", 200) != 200 or not success:
        reason = None
        try:
            reason = session.get_response_message(r)
        except Exception:
            reason = None
        if reason:
            service.send_message(message.chat.id,
                                 "⚠️ Warden refused the request: %s" % _esc(reason),
                                 parse_mode="HTML")
        else:
            service.send_message(message.chat.id,
                                 "Sorry, Warden refused the request.")
        return None

    # pull the job id out of the response payload
    try:
        data = session.get_response_json(r)
        job_id = data.get("job_id")
    except Exception:
        job_id = None
    if not job_id:
        service.send_message(message.chat.id,
                             "Warden accepted the request but didn't return a "
                             "job id.")
        return None
    return job_id


def _submit_and_ack(service, message, session, endpoint, payload,
                    queued_msg):
    """Submits a warden job fire-and-forget style.

    On a successful submit, sends a single "queued" acknowledgement
    (`queued_msg`) and returns True. If the submit is refused or fails,
    `_submit_job` has already surfaced the error and this returns False.
    """
    job_id = _submit_job(service, message, session, endpoint, payload)
    if job_id is None:
        return False

    service.send_message(message.chat.id, queued_msg, parse_mode="HTML")
    return True


# ------------------------------- Subcommands -------------------------------- #
def _cmd_scan(service, message, session, sub_args) -> bool:
    """`/net scan [cidr-or-range]` -> POST /scan/range (fire-and-forget)."""
    target = sub_args[0] if len(sub_args) > 0 else None
    payload = {} if target is None else {"target": target}
    if target:
        scope = "<code>%s</code>" % _esc(target)
    else:
        scope = "the local subnet"
    queued = ("✅ Queued a range scan of %s. Run <code>/net</code> shortly to "
              "see newly-discovered devices." % scope)
    return _submit_and_ack(service, message, session, "/scan/range", payload,
                           queued)


def _cmd_ports(service, message, session, sub_args) -> bool:
    """`/net ports <ip> [ports]` -> POST /scan/ports (fire-and-forget)."""
    if len(sub_args) < 1:
        service.send_message(message.chat.id,
                             "Usage: <code>/net ports &lt;ip&gt; [ports]</code>",
                             parse_mode="HTML")
        return False
    target = sub_args[0]
    payload = {"target": target}
    if len(sub_args) > 1:
        payload["ports"] = sub_args[1]
    queued = ("✅ Queued a port scan for <code>%s</code>. Run "
              "<code>/net %s</code> in a bit to see the results." %
              (_esc(target), _esc(target)))
    return _submit_and_ack(service, message, session, "/scan/ports", payload,
                           queued)


def _cmd_os(service, message, session, sub_args) -> bool:
    """`/net os <ip>` -> POST /scan/os (fire-and-forget)."""
    if len(sub_args) < 1:
        service.send_message(message.chat.id,
                             "Usage: <code>/net os &lt;ip&gt;</code>",
                             parse_mode="HTML")
        return False
    target = sub_args[0]
    payload = {"target": target}
    queued = ("✅ Queued OS detection for <code>%s</code>. Run "
              "<code>/net %s</code> in a bit to see the results." %
              (_esc(target), _esc(target)))
    return _submit_and_ack(service, message, session, "/scan/os", payload,
                           queued)


def _cmd_arppoison(service, message, session, sub_args) -> bool:
    """`/net arppoison <ip> [duration]` (alias `/net block`) -> POST /arppoison.

    Temporarily cuts a single local-subnet device off the LAN via an ARP-poison
    block. Warden enforces the guardrails (local-subnet only, duration clamped
    to [1, hard cap], and arpspoof must be installed); any refusal is surfaced
    to the user.
    """
    if len(sub_args) < 1:
        service.send_message(message.chat.id,
                             "Usage: <code>/net arppoison &lt;ip&gt; "
                             "[duration]</code>\nTemporarily cuts a device off "
                             "the LAN (local subnet only).",
                             parse_mode="HTML")
        return False

    target = sub_args[0]
    payload = {"target": target}
    duration = None
    if len(sub_args) > 1:
        try:
            duration = int(sub_args[1])
        except (TypeError, ValueError):
            service.send_message(message.chat.id,
                                 "The duration must be a whole number of "
                                 "seconds. It will be clamped to Warden's "
                                 "allowed range.")
            return False
        payload["duration"] = duration
    if duration is not None:
        queued = ("✅ Queued an ARP-poison block of <code>%s</code> for ~%ss. "
                  "It will run in the background." %
                  (_esc(target), _esc(duration)))
    else:
        queued = ("✅ Queued an ARP-poison block of <code>%s</code>. It will "
                  "run in the background." % _esc(target))
    return _submit_and_ack(service, message, session, "/arppoison", payload,
                           queued)


def _send_usage(service, message):
    """Sends the command usage / help text documenting every subcommand."""
    msg = (
        "🌐 <b>/network</b> (alias <code>/net</code>)\n\n"
        "<b>List devices</b>\n"
        "  <code>/net</code> — List all cached devices by last-seen time\n"
        "  <code>/net &lt;ip|mac|name&gt;</code> — Show one device's detail "
        "(open ports + detected OS)\n\n"
        "<b>Scan jobs</b>\n"
        "  <code>/net scan [cidr]</code> — Discover hosts on a range/CIDR "
        "(defaults to the local subnet)\n"
        "  <code>/net ports &lt;ip&gt; [ports]</code> — Scan a host for open "
        "ports\n"
        "  <code>/net os &lt;ip&gt;</code> — Detect a host's OS (Warden needs "
        "root; otherwise the job fails with a clear message)\n\n"
        "<b>ARP-poison block</b>\n"
        "  <code>/net arppoison &lt;ip&gt; [duration]</code> — Temporarily cut "
        "a device off the LAN (alias: <code>/net block</code>)\n"
        "  <i>Local subnet only; duration is clamped to Warden's allowed range"
        "; requires arpspoof installed on the "
        "Warden host.</i>\n\n"
        "<b>Examples:</b>\n"
        "  <code>/net scan 192.168.1.0/24</code>\n"
        "  <code>/net ports 192.168.1.10 22,80,443</code>\n"
        "  <code>/net os 192.168.1.10</code>\n"
        "  <code>/net arppoison 192.168.1.10 30</code>"
    )
    service.send_message(message.chat.id, msg, parse_mode="HTML")


# Map of job subcommands to their handler functions.
_JOB_SUBCOMMANDS = {
    "scan": _cmd_scan,
    "ports": _cmd_ports,
    "os": _cmd_os,
    "arppoison": _cmd_arppoison,
    "block": _cmd_arppoison,  # friendly alias for arppoison
}


# =================================== Main =================================== #
def command_network(service, message, args: list):
    # authenticate with warden (handles offline / auth-failure messaging)
    session = get_warden_session(service, message)
    if session is None:
        return False

    # `args[0]` is the command name itself; anything after it is a subcommand
    # or a device query. With no extra args, list all cached devices.
    if len(args) == 1:
        devices = get_devices(service, message, session)
        if devices is None:
            return False
        try:
            return network_list_times(service, message, args, devices)
        except Exception as e:
            service.send_message(message.chat.id,
                                 "Sorry, I couldn't retrieve network data. "
                                 "(%s)" % e)
            return False

    sub = args[1].strip().lower()
    sub_args = args[2:]

    # explicit help request
    if sub in ("help", "usage", "-h", "--help", "?"):
        _send_usage(service, message)
        return True

    # scan / port / OS / arppoison job subcommands
    if sub in _JOB_SUBCOMMANDS:
        return _JOB_SUBCOMMANDS[sub](service, message, session, sub_args)

    # otherwise, treat the argument as a device identifier for a detail view
    devices = get_devices(service, message, session)
    if devices is None:
        return False
    return _cmd_device_detail(service, message, session, devices, args[1])
