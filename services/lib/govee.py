# This module implements a light wrapper around the Govee Developer API (v2).
# It allows for direct interaction with Govee devices (lights and plugs) over
# the Govee cloud, using a single HTTPS round-trip per action instead of the
# multi-hop IFTTT chain previously used by `lumen`.
#
# The design mirrors the structure of `services/lib/lifx.py` (config class +
# wrapper class, refresh-delay caching, retry loop, `handle_error`) and
# `services/lib/wyze.py` (retry-with-sleep loop, device retrieval + toggle
# helpers), and reuses the `requests.Session()` pattern from
# `services/lib/ifttt.py`.
#
# Govee Developer API v2 reference:
#   https://developer.govee.com/reference/get-you-devices
#   https://developer.govee.com/reference/control-you-devices

# Imports
import os
import sys
import time
import uuid
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# Third-party imports
import requests


# =============================== Constants ================================= #
# Default base URL for the Govee Developer API v2. Overridable via config.
GOVEE_DEFAULT_BASE_URL = "https://openapi.api.govee.com"

# Default base URL for the legacy Govee Developer API v1. Overridable via
# config. Color is routed through v1 because the v2 `colorRgb` capability is
# accepted but is a physical no-op on at least the H6160 strip (verified live),
# whereas the v1 color command does physically change the device.
GOVEE_DEFAULT_V1_BASE_URL = "https://developer-api.govee.com"

# API endpoint paths (relative to the base URL).
GOVEE_PATH_DEVICES = "/router/api/v1/user/devices"
GOVEE_PATH_STATE = "/router/api/v1/device/state"
GOVEE_PATH_CONTROL = "/router/api/v1/device/control"

# Legacy v1 control endpoint path (relative to the v1 base URL). Used only for
# the color command; everything else stays on the v2 paths above.
GOVEE_V1_PATH_CONTROL = "/v1/devices/control"

# The v1 command name used to set an RGB color on a device.
GOVEE_V1_COLOR_CMD_NAME = "color"

# The v1 command name used to set the brightness on a device.
GOVEE_V1_BRIGHTNESS_CMD_NAME = "brightness"

# ------------------------- API version selection -------------------------- #
# String identifiers for the two Govee control API generations. These are used
# as the accepted values for the per-device `color_api` / `brightness_api`
# preferences (see `GoveeDevice`). A small set of module constants is used
# rather than bare string literals scattered through the code, matching the
# existing constant style (e.g. `GOVEE_ACTION_ON`/`GOVEE_ACTION_OFF`).
GOVEE_API_VERSION_V1 = "v1"
GOVEE_API_VERSION_V2 = "v2"

# The full set of accepted API-version values, used for validation.
GOVEE_API_VERSIONS = (GOVEE_API_VERSION_V1, GOVEE_API_VERSION_V2)

# Per-attribute default API versions, chosen from what was verified live:
#   * Color defaults to v1: the v2 `colorRgb` capability is accepted but is a
#     physical no-op on at least the H6160 strip, whereas the v1 color command
#     physically changes the device.
#   * Brightness defaults to v2: the v2 `range`/`brightness` capability is
#     confirmed working, so it remains the default.
GOVEE_DEFAULT_COLOR_API = GOVEE_API_VERSION_V1
GOVEE_DEFAULT_BRIGHTNESS_API = GOVEE_API_VERSION_V2


def normalize_api_version(value, default):
    """Normalizes a per-device API-version preference to a valid value.

    Accepts the caller-supplied `value` (typically read from a `devices` config
    entry) and returns it only if it is a recognized version string
    (case/whitespace-insensitive; one of `GOVEE_API_VERSIONS`). Any missing
    (`None`), non-string, or unrecognized value falls back to `default`.

    This is the single source of validation for the `color_api` /
    `brightness_api` preferences, shared by `GoveeDevice` and `lumen`.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in GOVEE_API_VERSIONS:
            return candidate
    return default

# Name of the authentication header expected by the Govee API.
GOVEE_API_KEY_HEADER = "Govee-API-Key"

# The HTTP status code and body `code` that both indicate success. The v2 API
# wraps a status value inside the JSON body (`"code": 200`) in addition to the
# HTTP status code, so both must be validated. The v1 API uses the same
# top-level `"code": 200` (paired with `"message": "Success"`), so the same
# code + HTTP-status validation applies to both.
GOVEE_SUCCESS_CODE = 200

# HTTP status code returned when the caller is rate limited.
GOVEE_HTTP_TOO_MANY_REQUESTS = 429

# Rate-limit response headers (observed on v1 and v2; parsed defensively as
# their presence on v2 is best-effort, not guaranteed).
GOVEE_HEADER_RATELIMIT_RESET = "API-RateLimit-Reset"
GOVEE_HEADER_RATELIMIT_REMAINING = "API-RateLimit-Remaining"
GOVEE_HEADER_RATELIMIT_LIMIT = "API-RateLimit-Limit"

# Maximum number of seconds we are willing to sleep while honoring a
# rate-limit reset header. This prevents a malformed/large `Reset` value from
# blocking the caller indefinitely.
GOVEE_MAX_BACKOFF_SECONDS = 60

# Default number of seconds to wait between successive control calls sent to
# the *same* device. The Govee cloud frequently rejects (rate-limits / reports
# "device busy") control commands that arrive too close together for a single
# device, which is why `lumen`'s power->color->brightness burst can drop the
# color/brightness commands. A short spacing lets each command land reliably.
GOVEE_DEFAULT_COMMAND_DELAY = 1.0

# Govee body `code` values that indicate a transient rate-limit / device-busy
# condition (as opposed to a permanent failure like a bad value or bad key).
# These are retriable with backoff rather than surfaced immediately. `429` is
# included because some device-busy responses arrive as an HTTP 200 envelope
# carrying a `429` body code.
GOVEE_RATE_LIMIT_BODY_CODES = (429,)

# Case-insensitive substrings in a Govee body `message` that indicate a
# transient rate-limit / device-busy condition. Matched defensively because
# the exact wording is not contractually guaranteed by the v2 API.
GOVEE_DEVICE_BUSY_INDICATORS = (
    "too many request",
    "rate limit",
    "device is busy",
    "device busy",
    "please try again",
)

# Capability descriptors (type + instance) for the actions `lumen` needs.
# See the v2 capability cheat-sheet in research report `9bfd09f2a82fb1c9`.
GOVEE_CAP_POWER_TYPE = "devices.capabilities.on_off"
GOVEE_CAP_POWER_INSTANCE = "powerSwitch"
GOVEE_CAP_BRIGHTNESS_TYPE = "devices.capabilities.range"
GOVEE_CAP_BRIGHTNESS_INSTANCE = "brightness"
GOVEE_CAP_COLOR_TYPE = "devices.capabilities.color_setting"
GOVEE_CAP_COLOR_INSTANCE = "colorRgb"

# Power values used by the `on_off`/`powerSwitch` capability.
GOVEE_POWER_ON_VALUE = 1
GOVEE_POWER_OFF_VALUE = 0

# Brightness bounds for the `range`/`brightness` capability. The v2 minimum is
# 1 (not 0), so a fully-scaled 0.0 float clamps up to 1.
GOVEE_BRIGHTNESS_MIN = 1
GOVEE_BRIGHTNESS_MAX = 100

# Brightness bounds for the legacy v1 `brightness` command. Unlike v2, the v1
# API accepts an integer percentage in the range 0-100 (0 is a valid value, so
# a fully-scaled 0.0 float maps to 0 rather than clamping up to 1). Documented
# per the Govee Developer API v1 reference.
GOVEE_V1_BRIGHTNESS_MIN = 0
GOVEE_V1_BRIGHTNESS_MAX = 100

# Per-channel bounds for RGB color components before packing.
GOVEE_COLOR_CHANNEL_MIN = 0
GOVEE_COLOR_CHANNEL_MAX = 255

# Accepted string actions for `set_device_power`.
GOVEE_ACTION_ON = "on"
GOVEE_ACTION_OFF = "off"


# ================================ Exceptions =============================== #
class GoveeError(Exception):
    """A typed error raised when a Govee API request fails.

    Carries the HTTP status code and the Govee body `code`/`message` (when
    available) so that callers (e.g. `lumen`) can log meaningful diagnostics.
    """

    def __init__(self, message, http_status=None, code=None):
        """Constructor.

        Arguments:
          message      A human-readable description of the failure.
          http_status  The HTTP status code returned by the API (or None).
          code         The Govee body `code` value returned by the API (or
                       None).
        """
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code


# ============================= Config Objects ============================== #
class GoveeConfig(Config):
    """A configuration object for creating a `Govee` client.

    Follows the field conventions of `LIFXConfig` and `WyzeConfig`. The
    `api_key` is a secret and must never be hardcoded; it is supplied only via
    per-host (git-ignored) configuration.
    """

    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            # The Govee Developer API key (secret; required).
            ConfigField("api_key",         [str],        required=True),
            # Base URL for the API (overridable for testing/proxying).
            ConfigField("base_url",        [str],        required=False,
                        default=GOVEE_DEFAULT_BASE_URL),
            # Base URL for the legacy v1 API, used only for the color command
            # (v2 colorRgb is a no-op on at least the H6160). Overridable for
            # testing/proxying.
            ConfigField("v1_base_url",     [str],        required=False,
                        default=GOVEE_DEFAULT_V1_BASE_URL),
            # Number of attempts for each request before giving up.
            ConfigField("retry_attempts",  [int],        required=False,
                        default=4),
            # Seconds to sleep between retries.
            ConfigField("retry_delay",     [int, float], required=False,
                        default=1),
            # Per-request HTTP timeout, in seconds.
            ConfigField("request_timeout", [int, float], required=False,
                        default=10),
            # Device-cache TTL, in seconds (mirrors LIFXConfig.refresh_delay).
            ConfigField("refresh_delay",   [int],        required=False,
                        default=7200),
            # Seconds to wait between successive control calls to the SAME
            # device, to avoid the Govee cloud rejecting commands sent too
            # close together (0 disables spacing).
            ConfigField("command_delay",   [int, float], required=False,
                        default=GOVEE_DEFAULT_COMMAND_DELAY),
            # Optional lumen-id -> Govee identity map. Each entry is a dict
            # with {id, sku, mac} plus optional per-device API-version
            # preferences `color_api` ("v1"/"v2", default "v1") and
            # `brightness_api` ("v1"/"v2", default "v2"). Missing/invalid
            # version values fall back to the defaults.
            ConfigField("devices",         [list],       required=False,
                        default=[]),
        ]


# ============================= Device Object =============================== #
class GoveeDevice:
    """Represents a single Govee device.

    Govee v2 identifies a device by its `sku` (model, e.g. "H6160") plus its
    `device` id (a MAC-format string). Both are required on every control
    call, so they are captured explicitly here rather than passing raw dicts
    around.
    """

    def __init__(self, sku, mac, name=None, capabilities=None, online=None,
                 color_api=None, brightness_api=None):
        """Constructor.

        Arguments:
          sku             The device model (e.g. "H6160", "H5083").
          mac             The Govee "device" id (MAC-format string).
          name            The Govee-app device name ("deviceName"), or None.
          capabilities    The raw capability descriptor list from
                          `/user/devices`, or None.
          online          Whether the device is online, or None if unknown.
          color_api       The API version ("v1"/"v2") to use for color control.
                          Missing/invalid values fall back to
                          `GOVEE_DEFAULT_COLOR_API` (v1).
          brightness_api  The API version ("v1"/"v2") to use for brightness
                          control. Missing/invalid values fall back to
                          `GOVEE_DEFAULT_BRIGHTNESS_API` (v2).
        """
        self.sku = sku
        self.mac = mac
        self.name = name
        self.capabilities = capabilities if capabilities is not None else []
        self.online = online
        # Per-device API-version preferences (validated + defaulted here so a
        # bare `GoveeDevice(sku, mac)` always has sensible values).
        self.color_api = normalize_api_version(color_api,
                                               GOVEE_DEFAULT_COLOR_API)
        self.brightness_api = normalize_api_version(brightness_api,
                                                    GOVEE_DEFAULT_BRIGHTNESS_API)

    def supports(self, cap_type, instance):
        """Returns True if this device advertises a capability matching the
        given `cap_type` and `instance`.

        When capability metadata is unavailable (empty list), this returns
        False, since support cannot be confirmed.
        """
        for cap in self.capabilities:
            if not isinstance(cap, dict):
                continue
            if cap.get("type") == cap_type and \
               cap.get("instance") == instance:
                return True
        return False

    def to_payload(self):
        """Returns the minimal `{"sku", "device"}` payload used to identify
        this device on state/control requests.
        """
        return {"sku": self.sku, "device": self.mac}

    @classmethod
    def from_api(cls, data):
        """Builds a `GoveeDevice` from a single `/user/devices` data entry.

        Arguments:
          data  A dict with at least `sku` and `device` keys, optionally
                `deviceName` and `capabilities`.
        """
        return cls(
            sku=data.get("sku"),
            mac=data.get("device"),
            name=data.get("deviceName"),
            capabilities=data.get("capabilities", []),
        )


# ============================= Govee Wrapper =============================== #
class Govee:
    """A thin `requests`-based client for the Govee Developer API v2.

    Provides a device-oriented interface consistent with `LIFX` and `Wyze`:
    device discovery with refresh-delay caching, name/address lookups, and
    power/brightness/color control (plus a plug-toggle convenience method).
    Value conversions from lumen's formats to the v2 API formats are performed
    internally.
    """

    def __init__(self, config: GoveeConfig):
        """Constructor.

        Builds a `requests.Session` pre-populated with the authentication and
        content-type headers used on every request. Initializes the device
        cache as empty.
        """
        self.config = config
        self.session = None
        self.devices = None
        self.last_refresh = None
        # Maps a device's `mac` -> monotonic timestamp of the last control call
        # sent to it, used to space successive per-device commands.
        self._last_control = {}
        self.refresh()

    # ------------------------------ Internals ------------------------------ #
    def refresh(self):
        """(Re)creates the internal `requests.Session` with default headers.

        Called on construction and after errors so that subsequent calls use a
        fresh session (mirrors `LIFX.refresh`).
        """
        session = requests.Session()
        session.headers[GOVEE_API_KEY_HEADER] = self.config.api_key
        session.headers["Content-Type"] = "application/json"
        self.session = session

    def handle_error(self, err):
        """Resets the session (so future calls use a fresh instance) and then
        re-raises the given error. Mirrors `LIFX.handle_error`.
        """
        self.refresh()
        raise err

    def _url(self, path, base_url=None):
        """Joins a base URL with the given endpoint path.

        Uses the configured v2 `base_url` by default; callers may pass an
        explicit `base_url` (e.g. the v1 base URL for the color command).
        """
        base = base_url if base_url is not None else self.config.base_url
        return "%s%s" % (base.rstrip("/"), path)

    def _ratelimit_backoff(self, resp):
        """Computes how many seconds to sleep after a 429 response.

        Honors the `API-RateLimit-Reset` header (interpreted as a UTC epoch
        second) when present and sensible, capped at
        `GOVEE_MAX_BACKOFF_SECONDS`. Falls back to `retry_delay` when no usable
        header is present. Parsing is defensive because header presence on v2
        is best-effort.
        """
        reset = resp.headers.get(GOVEE_HEADER_RATELIMIT_RESET)
        if reset is not None:
            try:
                # `Reset` is documented as the UTC epoch second at which the
                # window resets; sleep until then (bounded).
                seconds = float(reset) - time.time()
                if seconds > 0:
                    return min(seconds, GOVEE_MAX_BACKOFF_SECONDS)
                # A non-positive delta means the window already reset; use the
                # base retry delay rather than sleeping for zero time.
                return self.config.retry_delay
            except (TypeError, ValueError):
                # Malformed header value; fall through to the default delay.
                pass
        return self.config.retry_delay

    def _is_rate_limited(self, resp):
        """Returns True if a response indicates a transient rate-limit or
        device-busy condition that should be retried with backoff.

        Detects three forms observed from the Govee cloud:
          * an HTTP 429 status,
          * an HTTP 200 envelope carrying a rate-limit body `code`
            (`GOVEE_RATE_LIMIT_BODY_CODES`), and
          * a body `message` containing a known device-busy indicator
            (`GOVEE_DEVICE_BUSY_INDICATORS`).

        The latter two matter because Govee frequently reports "device busy" /
        "too many requests" inside a 200 envelope when commands are sent too
        close together to one device, rather than via a 429 status.
        """
        if resp.status_code == GOVEE_HTTP_TOO_MANY_REQUESTS:
            return True

        # Parse the body defensively; a non-JSON body is not a rate-limit hit.
        try:
            body = resp.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False

        if body.get("code") in GOVEE_RATE_LIMIT_BODY_CODES:
            return True

        message = body.get("message")
        if isinstance(message, str):
            lowered = message.lower()
            for indicator in GOVEE_DEVICE_BUSY_INDICATORS:
                if indicator in lowered:
                    return True

        return False

    def _validate(self, resp):
        """Validates a Govee API response.

        Treats a non-success HTTP status *or* a body `code` other than
        `GOVEE_SUCCESS_CODE` as a failure and raises `GoveeError`. Returns the
        parsed JSON body on success.
        """
        # Attempt to parse the JSON body; a missing/invalid body is itself an
        # error we surface via GoveeError.
        body = None
        try:
            body = resp.json()
        except ValueError:
            body = None

        # Validate the HTTP status code first.
        if resp.status_code != GOVEE_SUCCESS_CODE:
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else None
            raise GoveeError(
                "Govee API returned HTTP %s%s" %
                (resp.status_code,
                 (": %s" % message) if message else ""),
                http_status=resp.status_code,
                code=code,
            )

        # HTTP was OK; now validate the body `code` that v2 also returns.
        if not isinstance(body, dict):
            raise GoveeError(
                "Govee API returned a non-JSON or malformed body",
                http_status=resp.status_code,
                code=None,
            )

        code = body.get("code")
        if code != GOVEE_SUCCESS_CODE:
            raise GoveeError(
                "Govee API returned body code %s%s" %
                (code,
                 (": %s" % body.get("message")) if body.get("message")
                 else ""),
                http_status=resp.status_code,
                code=code,
            )

        return body

    def _request(self, method, path, json=None, base_url=None):
        """Performs an HTTP request with the retry/backoff loop and validation.

        Arguments:
          method    The HTTP method ("GET", "POST", or "PUT").
          path      The endpoint path (joined with a base URL).
          json      An optional JSON body (dict) to send.
          base_url  An optional base URL override (e.g. the v1 base URL for the
                    color command). Defaults to the configured v2 `base_url`.

        Returns the validated JSON body (dict) on success, or raises
        `GoveeError` after exhausting the retry attempts.
        """
        url = self._url(path, base_url=base_url)

        err = None
        for i in range(self.config.retry_attempts):
            try:
                resp = self.session.request(
                    method, url, json=json,
                    timeout=self.config.request_timeout,
                )

                # On a rate-limit / device-busy response, back off (honoring
                # headers where possible) and retry rather than failing. This
                # covers both an HTTP 429 and a "device busy"/"too many
                # requests" condition reported inside a 200 envelope.
                if self._is_rate_limited(resp):
                    body_code = None
                    try:
                        parsed = resp.json()
                        if isinstance(parsed, dict):
                            body_code = parsed.get("code")
                    except ValueError:
                        body_code = None
                    err = GoveeError(
                        "Govee API rate limit / device busy (HTTP %s)" %
                        resp.status_code,
                        http_status=resp.status_code,
                        code=body_code,
                    )
                    time.sleep(self._ratelimit_backoff(resp))
                    continue

                # Validate and return on success.
                return self._validate(resp)
            except GoveeError as e:
                # Application-level failures (bad status/body code) are
                # retried like transport errors, in case they are transient
                # (e.g. 5xx). The last error is re-raised on exhaustion.
                err = e
                time.sleep(self.config.retry_delay)
            except requests.RequestException as e:
                # Transport-level failures (timeouts, connection errors).
                err = e
                time.sleep(self.config.retry_delay)
                self.refresh()

        # All attempts exhausted; reset the session and re-raise.
        self.handle_error(err)

    # ---------------------------- Device Lookup ---------------------------- #
    def get_devices(self, refresh=False):
        """Retrieves and returns the list of `GoveeDevice`s on the account.

        The result is cached for `refresh_delay` seconds. A fresh fetch is
        performed only when `refresh` is True, the cache is empty, or the TTL
        has elapsed (mirrors `LIFX.get_lights`).
        """
        now = datetime.now()
        if refresh or \
           self.devices is None or \
           self.last_refresh is None or \
           dtu.diff_in_seconds(now, self.last_refresh) > \
           self.config.refresh_delay:

            body = self._request("GET", GOVEE_PATH_DEVICES)

            # Convert the raw data entries into GoveeDevice objects.
            data = body.get("data", []) if isinstance(body, dict) else []
            devices = []
            for entry in data:
                if isinstance(entry, dict):
                    devices.append(GoveeDevice.from_api(entry))

            self.devices = devices
            self.last_refresh = datetime.now()
            return self.devices

        return self.devices

    def get_device_by_name(self, name: str):
        """Finds a cached device whose Govee `deviceName` matches `name`.

        Returns the matching `GoveeDevice`, or None. Mirrors
        `LIFX.get_light_by_name`.
        """
        query = name.strip()
        for device in self.get_devices():
            if device.name is not None and device.name.strip() == query:
                return device
        return None

    def get_device_by_address(self, mac: str, sku: str):
        """Finds a device by its `mac` (and `sku`).

        If the device is present in the cache, the cached object (with full
        capability metadata) is returned. Otherwise a minimal `GoveeDevice`
        is constructed so control still works without a full list fetch.
        Mirrors `LIFX.get_light_by_address`.
        """
        query = mac.strip()
        for device in self.get_devices():
            if device.mac is not None and device.mac.strip() == query:
                # If a sku was supplied, require it to match as well.
                if sku is None or device.sku == sku:
                    return device

        # Not cached: build a minimal device from the supplied identity.
        return GoveeDevice(sku=sku, mac=mac)

    def get_device_state(self, device: GoveeDevice):
        """Queries and returns the raw state body for a device.

        Also updates the device's `online` flag when the state response
        includes an `online` capability. `POST /router/api/v1/device/state`.
        """
        body = self._request("POST", GOVEE_PATH_STATE, json={
            "requestId": str(uuid.uuid4()),
            "payload": device.to_payload(),
        })

        # Best-effort: extract the `online` capability so callers can detect
        # stale values. A device reporting online:false returns historical
        # data, but this is informational, not fatal.
        payload = body.get("payload", {}) if isinstance(body, dict) else {}
        for cap in payload.get("capabilities", []):
            if isinstance(cap, dict) and cap.get("instance") == "online":
                state = cap.get("state", {})
                device.online = state.get("value")
                break

        return body

    # --------------------------- Device Control ---------------------------- #
    def _apply_command_delay(self, device: GoveeDevice):
        """Spaces successive control calls to the *same* device.

        If a previous control call was sent to `device` less than
        `config.command_delay` seconds ago, sleeps for the remaining time so
        the Govee cloud does not reject the command as too-close/device-busy.
        The first command to a device (or any command after the delay window)
        proceeds immediately. A `command_delay` of 0 disables spacing.
        """
        delay = self.config.command_delay
        if not delay or delay <= 0:
            return

        key = device.mac
        last = self._last_control.get(key)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _record_command(self, device: GoveeDevice):
        """Records the current time as the last control call for `device`,
        used by `_apply_command_delay` to space subsequent commands."""
        self._last_control[device.mac] = time.monotonic()

    def _control(self, device: GoveeDevice, cap_type, instance, value):
        """Builds and sends a single control request for one capability.

        This is the single point that constructs the `/device/control` body:
        `{requestId, payload:{sku, device, capability:{type, instance,
        value}}}` (with a fresh uuid4 `requestId`), and validates the response.

        Successive control calls to the same device are spaced by
        `config.command_delay` (see `_apply_command_delay`) so that a
        power->color->brightness burst does not get rejected/dropped by the
        Govee cloud.

        Returns the validated JSON body on success.
        """
        # Space out back-to-back commands to the same device before sending.
        self._apply_command_delay(device)

        payload = device.to_payload()
        payload["capability"] = {
            "type": cap_type,
            "instance": instance,
            "value": value,
        }
        try:
            return self._request("POST", GOVEE_PATH_CONTROL, json={
                "requestId": str(uuid.uuid4()),
                "payload": payload,
            })
        finally:
            # Record the send time whether or not it succeeded, so a failed
            # command still spaces the next one to the same device.
            self._record_command(device)

    def _control_v1(self, device: GoveeDevice, cmd_name, value):
        """Builds and sends a single legacy v1 control request.

        This is the single point that constructs the v1 `/v1/devices/control`
        body (`{device, model, cmd:{name, value}}`) and sends it as a `PUT` to
        the configured `v1_base_url`. It applies the same per-device
        `command_delay` spacing and last-command recording as the v2 `_control`
        path, and relies on the shared `_request` retry/backoff + v1/v2
        response validation.

        Arguments:
          device    The target `GoveeDevice`.
          cmd_name  The v1 command name (e.g. "color", "brightness").
          value     The v1 command value (already converted/clamped).

        Returns the validated JSON body on success; raises `GoveeError` on
        failure (v1 success is HTTP 200 + a top-level `"code": 200` /
        `"message": "Success"` body).
        """
        # Space out back-to-back commands to the same device before sending,
        # mirroring the v2 `_control` path.
        self._apply_command_delay(device)

        body = {
            "device": device.mac,
            "model": device.sku,
            "cmd": {
                "name": cmd_name,
                "value": value,
            },
        }
        try:
            return self._request("PUT", GOVEE_V1_PATH_CONTROL, json=body,
                                 base_url=self.config.v1_base_url)
        finally:
            # Record the send time whether or not it succeeded, so a failed
            # command still spaces the next one to the same device.
            self._record_command(device)

    def set_device_power(self, device: GoveeDevice, action: str):
        """Turns a device on or off.

        `action` must be "on" or "off" (case/whitespace-insensitive), matching
        the form `lumen` uses (mirrors `LIFX.set_light_power`). Sends the
        `on_off`/`powerSwitch` capability with value 1 (on) or 0 (off).

        Power is always controlled via the v2 API (not configurable per-device),
        since v2 power is confirmed working.
        """
        action = action.strip().lower()
        assert action in [GOVEE_ACTION_ON, GOVEE_ACTION_OFF], \
            "action must be \"on\" or \"off\""

        value = GOVEE_POWER_ON_VALUE if action == GOVEE_ACTION_ON \
            else GOVEE_POWER_OFF_VALUE
        return self._control(device, GOVEE_CAP_POWER_TYPE,
                             GOVEE_CAP_POWER_INSTANCE, value)

    def set_device_brightness(self, device: GoveeDevice, brightness: float):
        """Sets a device's brightness via the device's configured API version.

        Accepts lumen's 0.0-1.0 float and dispatches based on
        `device.brightness_api` (default "v2"):

          * v2 -> the `range`/`brightness` capability with an integer 1-100.
          * v1 -> the legacy `PUT {v1_base_url}/v1/devices/control` with
            `{cmd:{name:"brightness", value:N}}` where N is an integer 0-100.

        The 0.0-1.0 float is converted to each API's expected integer scale and
        clamped (v2 minimum is 1; v1 minimum is 0). Returns the validated JSON
        body on success.
        """
        if device.brightness_api == GOVEE_API_VERSION_V1:
            value = self._brightness_to_v1_api(brightness)
            return self._control_v1(device, GOVEE_V1_BRIGHTNESS_CMD_NAME, value)

        value = self._brightness_to_api(brightness)
        return self._control(device, GOVEE_CAP_BRIGHTNESS_TYPE,
                             GOVEE_CAP_BRIGHTNESS_INSTANCE, value)

    def set_device_color(self, device: GoveeDevice, color):
        """Sets a device's RGB color via the device's configured API version.

        Accepts lumen's `[r, g, b]` list (each 0-255) and dispatches based on
        `device.color_api` (default "v1"):

          * v1 -> the legacy `PUT {v1_base_url}/v1/devices/control` with
            `{cmd:{name:"color", value:{r,g,b}}}` (raw per-channel ints).
          * v2 -> the `color_setting`/`colorRgb` capability with the packed
            integer `(r << 16) | (g << 8) | b`.

        Color defaults to v1 because the v2 `colorRgb` capability is accepted
        by the API but is a physical no-op on at least the H6160 strip (verified
        live), whereas the v1 color command does change the device. Channels are
        clamped to [0, 255] in both paths.

        Successive control calls to the same device are spaced by
        `config.command_delay` (see `_apply_command_delay`) on both paths.

        Returns the validated JSON body on success; raises `GoveeError` on
        failure.
        """
        r, g, b = self._color_channels(color)

        if device.color_api == GOVEE_API_VERSION_V2:
            packed = (r << 16) | (g << 8) | b
            return self._control(device, GOVEE_CAP_COLOR_TYPE,
                                 GOVEE_CAP_COLOR_INSTANCE, packed)

        return self._control_v1(device, GOVEE_V1_COLOR_CMD_NAME,
                                {"r": r, "g": g, "b": b})

    def toggle_plug(self, device: GoveeDevice, power_on: bool):
        """Convenience wrapper over `set_device_power` for H5083 plugs.

        Matches the `Wyze.toggle_plug` naming: turns the plug on when
        `power_on` is True, otherwise off.
        """
        action = GOVEE_ACTION_ON if power_on else GOVEE_ACTION_OFF
        return self.set_device_power(device, action)

    # --------------------------- Value Helpers ----------------------------- #
    @staticmethod
    def _brightness_to_api(brightness: float):
        """Converts a lumen 0.0-1.0 brightness float to a v2 int (1-100).

        The result is rounded and clamped so that values below the API minimum
        (1) or above the maximum (100) are corrected rather than rejected.
        """
        value = int(round(brightness * GOVEE_BRIGHTNESS_MAX))
        # Clamp into the API-accepted range [1, 100].
        value = max(GOVEE_BRIGHTNESS_MIN, min(GOVEE_BRIGHTNESS_MAX, value))
        return value

    @staticmethod
    def _brightness_to_v1_api(brightness: float):
        """Converts a lumen 0.0-1.0 brightness float to a legacy v1 int (0-100).

        Mirrors `_brightness_to_api` but uses the v1 range, whose minimum is 0
        (not 1). The result is rounded and clamped so that out-of-range values
        are corrected rather than rejected; a fully-scaled 0.0 float therefore
        maps to 0 (v1) rather than 1 (v2).
        """
        value = int(round(brightness * GOVEE_V1_BRIGHTNESS_MAX))
        # Clamp into the v1-accepted range [0, 100].
        value = max(GOVEE_V1_BRIGHTNESS_MIN,
                    min(GOVEE_V1_BRIGHTNESS_MAX, value))
        return value

    @staticmethod
    def _color_channels(color):
        """Converts a lumen `[r, g, b]` list (0-255) to a `(r, g, b)` tuple of
        ints, each clamped to [0, 255].

        This is the form used by the v1 color command (raw per-channel ints).
        """
        def clamp_channel(component):
            return max(GOVEE_COLOR_CHANNEL_MIN,
                       min(GOVEE_COLOR_CHANNEL_MAX, int(component)))

        return (clamp_channel(color[0]),
                clamp_channel(color[1]),
                clamp_channel(color[2]))

    @staticmethod
    def _color_to_api(color):
        """Converts a lumen `[r, g, b]` list (0-255) to a packed v2 int.

        Each channel is clamped to [0, 255] before packing as
        `(r << 16) | (g << 8) | b`. This is the value shape used by the v2
        `color_setting`/`colorRgb` capability, exercised when a device selects
        `color_api == "v2"`.
        """
        r, g, b = Govee._color_channels(color)
        return (r << 16) | (g << 8) | b
