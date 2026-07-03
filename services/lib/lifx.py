# This module implements a light wrapper around the LIFX LAN library. This
# allows for the interaction with LIFX devices on the LAN (Local Area Network).

# Imports
import os
import sys
from datetime import datetime
import time
import threading
import colorsys

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# LIFX imports
from lifxlan import LifxLAN, Light

# Default seconds to wait between successive LIFX LAN commands (across all
# bulbs) so that a burst of per-bulb power commands from multiple worker
# threads is not sent in the same instant, reducing Wi-Fi/UDP contention on
# clustered bulbs. Mirrors the Govee `command_delay` precedent. Kept small so
# toggles still feel responsive; set to 0 to disable staggering.
LIFX_DEFAULT_COMMAND_DELAY = 0.05


class LIFXError(Exception):
    """Raised when a LIFX command cannot be verified as successful (e.g. a
    power-state read-back does not match the requested state)."""
    pass


class LIFXConfig(Config):
    """An object used to configure the LIFX object."""
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("refresh_delay",    [int],  required=False, default=7200),
            ConfigField("retry_attempts",   [int],  required=False, default=4),
            ConfigField("retry_delay",      [int, float],  required=False, default=0.1),
            # Seconds to wait between successive LIFX LAN commands (across all
            # bulbs) so 5+ bulbs are not commanded in the same instant. A value
            # of 0 disables the stagger. See `_apply_command_delay`.
            ConfigField("command_delay",    [int, float],  required=False,
                        default=LIFX_DEFAULT_COMMAND_DELAY)
        ]

class LIFX:
    """The wrapper around the LIFX LAN SDK."""
    def __init__(self, config: LIFXConfig):
        """Constructor."""
        self.lifx = LifxLAN()
        self.config = config

        self.lights = None
        self.last_refresh = None

        # A single reentrant lock that serializes ALL access to the underlying
        # `lifxlan` objects (the shared `LifxLAN` and its cached `Light`/
        # `Device` sockets). `lifxlan`'s `Device` manages UDP sockets through a
        # process-global, UN-locked `socket_table`/`socket_counter`; when
        # multiple worker threads open/close those sockets (or one thread
        # rebuilds `self.lifx` via `refresh()`) concurrently, sockets get torn
        # down out from under in-flight commands, producing
        # `[Errno 9] Bad file descriptor`. Holding this lock around the full
        # body of every public operation that touches lifxlan serializes LAN
        # access, eliminating the cross-thread socket race. LIFX LAN calls are
        # fast, so serializing a handful of bulbs is imperceptible. An `RLock`
        # is used so nested internal calls (e.g. `get_light_by_name` ->
        # `_find_light_by_name` -> `get_lights`, or any op -> `handle_error` ->
        # `refresh`) can re-acquire the lock without deadlocking.
        self._lifx_lock = threading.RLock()

        # Tracks the monotonic time of the last LIFX LAN command sent, plus a
        # lock guarding it, so `_apply_command_delay` can gently stagger
        # commands. NOTE: the real serialization guarantee now comes from
        # `_lifx_lock` above; this stagger is retained as a small, optional
        # spacing knob (`command_delay`) and no longer the primary safety net.
        self._command_lock = threading.Lock()
        self._last_command = None

    def refresh(self):
        """Rebuilds the shared `LifxLAN` object. This tears down the old
        object's sockets, so it MUST only run while holding `_lifx_lock` (i.e.
        while no other thread is mid-command on the old object/sockets). It is
        reserved for genuine discovery problems; a lost ack on a normal command
        is simply retried on the SAME object rather than rebuilt here.
        """
        with self._lifx_lock:
            self.lifx = LifxLAN()

    def handle_error(self, err):
        """Takes in an error, resets the LifxLAN object (for future calls to use a
        fresh instance, in case this helps avoid unexpected errors), then throws
        the error.
        """
        with self._lifx_lock:
            self.refresh()
            raise err

    def get_lights(self, refresh=False):
        """Retrieves and returns a list of online light objects."""
        with self._lifx_lock:
            # we only want to perform the LAN search if we've reached out refresh
            # time, or if the caller forces our hand
            now = datetime.now()
            if refresh or \
               self.last_refresh is None or \
               dtu.diff_in_seconds(now, self.last_refresh) > self.config.refresh_delay:

                err = None
                all_lights = {}
                for i in range(self.config.retry_attempts):
                    try:
                        # retrieve all lights; for each attempt, build a list of
                        # unique lights discovered by adding only new ones we
                        # haven't seen before
                        #
                        # (light discovery seems to be flaky, so these retries aim
                        # to collect as many as possible)
                        lights = self.lifx.get_lights()
                        for l in lights:
                            label = l.get_label()

                            # if we've seen this light already, skip it
                            if label in all_lights:
                                continue

                            # otherwise, add it to the light dictionary
                            all_lights[label] = l
                    except Exception as e:
                        # A discovery failure IS a genuine LAN-discovery problem,
                        # so rebuilding the shared object between attempts is
                        # legitimate here. It is safe because we hold
                        # `_lifx_lock`: no other thread can be mid-command on the
                        # object whose sockets `refresh()` tears down.
                        err = e
                        time.sleep(self.config.retry_delay)
                        self.refresh()

                # if an error occurred, handle it
                if err is not None:
                    self.handle_error(err)

                # otherwise, convert the dictionary of lights to a list and return
                self.lights = list(all_lights.values())
                self.last_refresh = datetime.now()
                return self.lights

            return self.lights

    def _find_light_by_name(self, query: str, refresh: bool = False):
        """Looks up a light by its (already-stripped) label. When `refresh` is
        True, forces a fresh LAN discovery before scanning; otherwise uses the
        cache-first path. Returns the matching light or None. Discovery errors
        are handled (and re-raised) via `handle_error`.
        """
        err = None
        with self._lifx_lock:
            try:
                # retrieve the list of lights, then iterate through them and
                # search for a light with a matching name
                lights = self.get_lights(refresh=refresh)
                for l in lights:
                    if l.get_label().strip() == query:
                        return l
                return None
            except Exception as e:
                # `get_lights` already retried/refreshed for genuine discovery
                # failures; do NOT refresh again here (that would needlessly
                # tear down the shared object's sockets). Just surface the error.
                err = e

        # if we reached here, handle the error
        self.handle_error(err)

    def get_light_by_name(self, name: str):
        """Attempts to retrieve and find a light by its name. Returns the matching
        object, or None.

        On a cache miss, forces exactly one fresh LAN discovery and re-checks
        before giving up. This makes the lookup self-healing when a bulb was
        missed during an earlier (flaky) discovery pass and is still absent
        from the stale cache, without risking an infinite re-discovery loop.
        """
        query = name.strip()

        with self._lifx_lock:
            # first, try the normal cache-first path (honors the 2h refresh_delay)
            match = self._find_light_by_name(query, refresh=False)
            if match is not None:
                return match

            # cache miss: force ONE fresh discovery and re-check. Because we only
            # force a refresh a single time (not in a loop), a truly-absent bulb
            # still returns None cleanly after this second attempt.
            return self._find_light_by_name(query, refresh=True)

    def get_light_by_address(self, macaddr: str, ipaddr: str):
        """Attempts to retrieve and find a light by its MAC and IP addresses.

        Returns the matching object, or None.
        """
        err = None
        with self._lifx_lock:
            for i in range(self.config.retry_attempts):
                try:
                    # create a light object directly, using the given MAC and IP
                    # addresses
                    return Light(macaddr, ipaddr)
                except Exception as e:
                    # constructing a `Light` does not depend on the shared
                    # `LifxLAN` discovery object, so rebuilding it would not
                    # help; just back off and retry on the same state.
                    err = e
                    time.sleep(self.config.retry_delay)
        self.handle_error(err)

    def _apply_command_delay(self):
        """Gently staggers/serializes successive LIFX LAN commands.

        If a previous command was sent less than `config.command_delay` seconds
        ago, sleeps for the remaining time so that a burst of per-bulb commands
        (e.g. the 5 kitchen bulbs commanded by concurrent worker threads) is
        spread out instead of hitting the air simultaneously. The lock is held
        across the sleep so concurrent callers are spaced relative to each
        other. A `command_delay` of 0 (or less) disables the stagger.
        """
        delay = self.config.command_delay
        if not delay or delay <= 0:
            return

        with self._command_lock:
            now = time.monotonic()
            if self._last_command is not None:
                remaining = delay - (now - self._last_command)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_command = time.monotonic()

    @staticmethod
    def _power_matches(actual, action: str) -> bool:
        """Returns True if a `get_power()` read-back (`actual`) matches the
        requested `action` ("on"/"off"). LIFX reports power as a level in
        [0, 65535]; any non-zero level is considered "on".
        """
        if actual is None:
            return False
        try:
            level = int(actual)
        except (TypeError, ValueError):
            return False
        if action == "on":
            return level > 0
        return level == 0

    def set_light_power(self, light: Light, action: str):
        """Toggles a light on or off, using an *acknowledged* send and verifying
        the resulting power state.

        Unlike a fire-and-forget (`rapid=True`) send, this requests an
        acknowledgement (`rapid=False`), so a lost/timed-out command raises and
        the retry loop actually engages. After each attempt the power state is
        read back and compared to the requested state; a mismatch (or a failed
        read-back) is treated as a retryable failure. If all `retry_attempts`
        are exhausted, the last error is raised via `handle_error`.
        """
        action = action.strip().lower()
        assert action in ["on", "off"]

        err = None
        with self._lifx_lock:
            for i in range(self.config.retry_attempts):
                try:
                    # stagger this command relative to other LIFX commands so a
                    # burst of per-bulb toggles is not sent all at once
                    self._apply_command_delay()

                    # turn the light on or off with an acknowledged send: rapid=False
                    # makes lifxlan request an ack and raise on timeout/loss
                    light.set_power(action, rapid=False)

                    # verify the change actually took effect by reading the power
                    # state back; a get failure raises and is treated as retryable
                    actual = light.get_power()
                    if not self._power_matches(actual, action):
                        raise LIFXError(
                            "power state verification failed after setting to \"%s\" "
                            "(read back %r)" % (action, actual)
                        )
                    return
                except Exception as e:
                    # A lost ack / verification miss is a TRANSIENT command
                    # failure, NOT a discovery problem: retry on the SAME
                    # `light`/`LifxLAN` object. Deliberately do NOT call
                    # `refresh()` here -- rebuilding the shared object would tear
                    # down sockets that other worker threads may hold in flight,
                    # which is exactly the `[Errno 9] Bad file descriptor` bug we
                    # are fixing.
                    err = e
                    time.sleep(self.config.retry_delay)
        self.handle_error(err)

    def set_light_color(self, light: Light, color):
        # LIFX LAN accepts color as a list of:
        #
        #   [
        #       hue (0-65535),
        #       saturation (0-65535),
        #       brightness (0-65535),
        #       kelvn (2500-9000)
        #   ]
        #
        # We need to convert the RGB and brightness to these values.

        # normalize the RGB values such that they are on a [0.0, 1.0] scale,
        # then use them to convert to HSV/HSB
        r = color[0] / 255.0
        g = color[1] / 255.0
        b = color[2] / 255.0
        hsv = list(colorsys.rgb_to_hsv(color[0], color[1], color[2]))

        # scale all three values to the scale of [0, 65535]
        hsv[0] = hsv[0] * 65535.0
        hsv[1] = hsv[1] * 65535.0
        hsv[2] = (hsv[2] * 65535.0) / 255.0

        # finally, set the kelvin value (currently setting this to zero and the
        # API seems to be taking care of things), then form the final array to
        # pass to the LIFX object
        kelvin = 0.0
        newcolors = hsv + [kelvin]

        err = None
        with self._lifx_lock:
            for i in range(self.config.retry_attempts):
                try:
                    # apply the change
                    light.set_color(newcolors, rapid=True)
                    return
                except Exception as e:
                    # transient command failure: retry on the SAME object rather
                    # than tearing down the shared `LifxLAN` sockets mid-flight
                    err = e
                    time.sleep(self.config.retry_delay)
        self.handle_error(err)

    def set_light_brightness(self, light: Light, brightness: float):
        # convert the brightness float to an integer between 0 and 65535
        brightness_int = int(brightness * 65535.0)

        # pass this into the LIFX LAN protocl library function, holding the LAN
        # lock so this does not race with other threads' socket usage
        with self._lifx_lock:
            light.set_brightness(brightness_int, rapid=True)

