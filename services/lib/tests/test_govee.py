# Tests for the Govee library wrapper defined in `services/lib/govee.py`.
#
# These tests exercise `GoveeConfig`, `GoveeDevice`, `Govee`, and `GoveeError`
# entirely against a mocked HTTP layer (`requests.Session`) so that no real
# network calls are ever made. They cover request shaping (URLs, headers,
# bodies), the brightness/color conversions, HTTP-status and body-`code`
# validation, retry + 429 backoff behavior, device caching/refresh, name and
# address lookups, and config required-field enforcement.

import os
import sys
import unittest
from unittest import mock

# Enable imports from the services directory (mirrors sibling test files).
sdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if sdir not in sys.path:
    sys.path.append(sdir)

import requests

from lib.govee import (
    Govee,
    GoveeConfig,
    GoveeDevice,
    GoveeError,
    normalize_api_version,
    GOVEE_API_KEY_HEADER,
    GOVEE_PATH_DEVICES,
    GOVEE_PATH_STATE,
    GOVEE_PATH_CONTROL,
    GOVEE_V1_PATH_CONTROL,
    GOVEE_DEFAULT_BASE_URL,
    GOVEE_DEFAULT_V1_BASE_URL,
    GOVEE_DEFAULT_COMMAND_DELAY,
    GOVEE_API_VERSION_V1,
    GOVEE_API_VERSION_V2,
    GOVEE_DEFAULT_COLOR_API,
    GOVEE_DEFAULT_BRIGHTNESS_API,
    GOVEE_V1_BRIGHTNESS_CMD_NAME,
    GOVEE_V1_COLOR_CMD_NAME,
)


# ============================== Test Helpers =============================== #
TEST_API_KEY = "test-api-key-0000"


class FakeResponse:
    """A minimal stand-in for a `requests.Response` object."""

    def __init__(self, status_code=200, json_body=None, headers=None,
                 raise_json=False):
        """Constructor.

        Arguments:
          status_code  The HTTP status code to report.
          json_body    The object returned by `.json()`.
          headers      A dict of response headers.
          raise_json   When True, `.json()` raises ValueError (bad body).
        """
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers if headers is not None else {}
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no json body")
        return self._json_body


class FakeSession:
    """A fake `requests.Session` that records requests and returns queued
    responses.

    The `.headers` dict mimics the real session so header assertions work. The
    `responses` list is consumed one entry per `request()` call; each entry may
    be a `FakeResponse` or an exception instance to raise.
    """

    def __init__(self, responses):
        self.headers = {}
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        # Record the call for later assertions.
        self.calls.append({
            "method": method,
            "url": url,
            "json": json,
            "timeout": timeout,
            "headers": dict(self.headers),
        })

        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_config(**overrides):
    """Builds a `GoveeConfig` with sensible test defaults, applying any
    overrides. Uses `from_json` so required-field validation runs.
    """
    data = {"api_key": TEST_API_KEY, "retry_delay": 0, "command_delay": 0}
    data.update(overrides)
    return GoveeConfig.from_json(data)


def make_govee(responses, **config_overrides):
    """Builds a `Govee` client whose session is replaced by a `FakeSession`
    seeded with the given queued responses.

    The fake session is seeded with the same default headers the real
    `Govee.refresh` installs (notably the `Govee-API-Key` auth header) so that
    header assertions on recorded requests remain meaningful.
    """
    govee = Govee(make_config(**config_overrides))
    fake = FakeSession(responses)
    # Mirror the headers the real session carries so recorded requests reflect
    # what would actually be sent on the wire.
    fake.headers.update(govee.session.headers)
    govee.session = fake
    return govee


def success_body(data=None):
    """Returns a well-formed v2 success body."""
    body = {"code": 200, "message": "success"}
    if data is not None:
        body["data"] = data
    return body


def v1_success_body():
    """Returns a well-formed legacy v1 success body.

    The v1 control API reports success as HTTP 200 with a top-level
    `"code": 200` and `"message": "Success"` (note the capital S) alongside an
    (empty) `"data"` object.
    """
    return {"code": 200, "message": "Success", "data": {}}


# ============================== Config Tests =============================== #
class TestGoveeConfig(unittest.TestCase):
    """Tests for `GoveeConfig` field handling."""

    def test_missing_api_key_raises(self):
        """A config without the required `api_key` must fail to parse."""
        with self.assertRaises(Exception):
            GoveeConfig.from_json({"base_url": "https://example.com"})

    def test_defaults_applied(self):
        """Optional fields should receive their documented defaults."""
        cfg = GoveeConfig.from_json({"api_key": TEST_API_KEY})
        self.assertEqual(cfg.api_key, TEST_API_KEY)
        self.assertEqual(cfg.base_url, GOVEE_DEFAULT_BASE_URL)
        self.assertEqual(cfg.v1_base_url, GOVEE_DEFAULT_V1_BASE_URL)
        self.assertEqual(cfg.retry_attempts, 4)
        self.assertEqual(cfg.retry_delay, 1)
        self.assertEqual(cfg.request_timeout, 10)
        self.assertEqual(cfg.refresh_delay, 7200)
        self.assertEqual(cfg.command_delay, GOVEE_DEFAULT_COMMAND_DELAY)
        self.assertEqual(cfg.devices, [])

    def test_overrides_respected(self):
        """Provided optional values should override defaults."""
        cfg = GoveeConfig.from_json({
            "api_key": TEST_API_KEY,
            "base_url": "https://proxy.local",
            "v1_base_url": "https://v1.proxy.local",
            "retry_attempts": 2,
            "request_timeout": 3,
        })
        self.assertEqual(cfg.base_url, "https://proxy.local")
        self.assertEqual(cfg.v1_base_url, "https://v1.proxy.local")
        self.assertEqual(cfg.retry_attempts, 2)
        self.assertEqual(cfg.request_timeout, 3)


# ============================== Device Tests =============================== #
class TestGoveeDevice(unittest.TestCase):
    """Tests for the `GoveeDevice` representation."""

    def test_to_payload(self):
        """`to_payload` returns the sku/device identity dict."""
        d = GoveeDevice(sku="H6160", mac="AA:BB")
        self.assertEqual(d.to_payload(), {"sku": "H6160", "device": "AA:BB"})

    def test_supports_matches_capability(self):
        """`supports` returns True only for advertised capabilities."""
        caps = [{"type": "devices.capabilities.on_off",
                 "instance": "powerSwitch"}]
        d = GoveeDevice(sku="H5083", mac="AA", capabilities=caps)
        self.assertTrue(d.supports("devices.capabilities.on_off",
                                   "powerSwitch"))
        self.assertFalse(d.supports("devices.capabilities.range",
                                    "brightness"))

    def test_from_api(self):
        """`from_api` maps the raw device entry fields correctly."""
        entry = {
            "sku": "H6160",
            "device": "AA:BB:CC",
            "deviceName": "Staircase",
            "capabilities": [{"type": "t", "instance": "i"}],
        }
        d = GoveeDevice.from_api(entry)
        self.assertEqual(d.sku, "H6160")
        self.assertEqual(d.mac, "AA:BB:CC")
        self.assertEqual(d.name, "Staircase")
        self.assertEqual(len(d.capabilities), 1)


# ========================== Session / Header Tests ========================= #
class TestGoveeSession(unittest.TestCase):
    """Tests that the session is created with the correct auth headers."""

    def test_auth_header_set_on_session(self):
        """The `Govee-API-Key` and content-type headers must be present."""
        govee = Govee(make_config())
        self.assertEqual(govee.session.headers[GOVEE_API_KEY_HEADER],
                         TEST_API_KEY)
        self.assertEqual(govee.session.headers["Content-Type"],
                         "application/json")


# ========================== Request Shaping Tests ========================== #
class TestRequestShaping(unittest.TestCase):
    """Tests that requests target the correct URLs with correct bodies."""

    def test_get_devices_request(self):
        """`get_devices` issues a GET to the devices endpoint with a timeout."""
        govee = make_govee([
            FakeResponse(200, success_body(data=[
                {"sku": "H6160", "device": "AA", "deviceName": "Strip",
                 "capabilities": []},
            ])),
        ])
        devices = govee.get_devices()

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_BASE_URL + GOVEE_PATH_DEVICES)
        self.assertEqual(call["timeout"], 10)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].sku, "H6160")

    def test_control_request_body_shape(self):
        """A control call POSTs the documented body with a requestId."""
        govee = make_govee([FakeResponse(200, success_body())])
        device = GoveeDevice(sku="H6160", mac="AA:BB")
        govee.set_device_power(device, "on")

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_BASE_URL + GOVEE_PATH_CONTROL)
        body = call["json"]
        # requestId must be present and non-empty.
        self.assertIn("requestId", body)
        self.assertTrue(body["requestId"])
        # payload must carry sku, device, and the capability structure.
        payload = body["payload"]
        self.assertEqual(payload["sku"], "H6160")
        self.assertEqual(payload["device"], "AA:BB")
        cap = payload["capability"]
        self.assertEqual(cap["type"], "devices.capabilities.on_off")
        self.assertEqual(cap["instance"], "powerSwitch")
        self.assertEqual(cap["value"], 1)

    def test_power_off_value(self):
        """Turning off sends value 0."""
        govee = make_govee([FakeResponse(200, success_body())])
        govee.set_device_power(GoveeDevice("H5083", "AA"), "off")
        cap = govee.session.calls[0]["json"]["payload"]["capability"]
        self.assertEqual(cap["value"], 0)

    def test_power_invalid_action_asserts(self):
        """An invalid action raises AssertionError before any request."""
        govee = make_govee([])
        with self.assertRaises(AssertionError):
            govee.set_device_power(GoveeDevice("H5083", "AA"), "blink")

    def test_state_request_shape(self):
        """`get_device_state` posts to the state endpoint with identity."""
        state_body = {
            "code": 200,
            "message": "success",
            "payload": {
                "capabilities": [
                    {"type": "devices.capabilities.online",
                     "instance": "online",
                     "state": {"value": True}},
                ],
            },
        }
        govee = make_govee([FakeResponse(200, state_body)])
        device = GoveeDevice("H6160", "AA:BB")
        govee.get_device_state(device)

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_BASE_URL + GOVEE_PATH_STATE)
        self.assertEqual(call["json"]["payload"],
                         {"sku": "H6160", "device": "AA:BB"})
        # online should be populated from the state response.
        self.assertTrue(device.online)

    def test_unique_request_ids(self):
        """Each control call should generate a distinct requestId."""
        govee = make_govee([
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
        ])
        device = GoveeDevice("H6160", "AA")
        govee.set_device_power(device, "on")
        govee.set_device_power(device, "off")
        id1 = govee.session.calls[0]["json"]["requestId"]
        id2 = govee.session.calls[1]["json"]["requestId"]
        self.assertNotEqual(id1, id2)


# =========================== Conversion Tests ============================== #
class TestBrightnessConversion(unittest.TestCase):
    """Tests for the lumen-float -> v2-int brightness conversion."""

    def _brightness_value(self, brightness):
        govee = make_govee([FakeResponse(200, success_body())])
        govee.set_device_brightness(GoveeDevice("H6160", "AA"), brightness)
        return govee.session.calls[0]["json"]["payload"]["capability"]["value"]

    def test_full_brightness(self):
        """1.0 maps to 100."""
        self.assertEqual(self._brightness_value(1.0), 100)

    def test_zero_clamps_to_min(self):
        """0.0 clamps up to the API minimum of 1."""
        self.assertEqual(self._brightness_value(0.0), 1)

    def test_half_brightness(self):
        """0.5 maps to 50."""
        self.assertEqual(self._brightness_value(0.5), 50)

    def test_over_range_clamps_to_max(self):
        """Values above 1.0 clamp to 100."""
        self.assertEqual(self._brightness_value(1.5), 100)

    def test_instance_and_type(self):
        """Brightness uses the range/brightness capability."""
        govee = make_govee([FakeResponse(200, success_body())])
        govee.set_device_brightness(GoveeDevice("H6160", "AA"), 0.5)
        cap = govee.session.calls[0]["json"]["payload"]["capability"]
        self.assertEqual(cap["type"], "devices.capabilities.range")
        self.assertEqual(cap["instance"], "brightness")


class TestColorConversion(unittest.TestCase):
    """Tests for the lumen-[r,g,b] -> v1 raw-channel color request.

    Color is sent via the legacy v1 control API (`PUT /v1/devices/control`)
    because the v2 `colorRgb` capability is a physical no-op on at least the
    H6160. The channels are sent as raw (clamped) ints, not packed.
    """

    def _color_body(self, color, **overrides):
        govee = make_govee([FakeResponse(200, v1_success_body())],
                           **overrides)
        govee.set_device_color(GoveeDevice("H6160", "AA"), color)
        return govee.session.calls[0]["json"]["cmd"]["value"]

    def test_red(self):
        """[255,0,0] -> {r:255,g:0,b:0}."""
        self.assertEqual(self._color_body([255, 0, 0]),
                         {"r": 255, "g": 0, "b": 0})

    def test_green(self):
        """[0,255,0] -> {r:0,g:255,b:0}."""
        self.assertEqual(self._color_body([0, 255, 0]),
                         {"r": 0, "g": 255, "b": 0})

    def test_blue(self):
        """[0,0,255] -> {r:0,g:0,b:255}."""
        self.assertEqual(self._color_body([0, 0, 255]),
                         {"r": 0, "g": 0, "b": 255})

    def test_white(self):
        """[255,255,255] -> {r:255,g:255,b:255}."""
        self.assertEqual(self._color_body([255, 255, 255]),
                         {"r": 255, "g": 255, "b": 255})

    def test_channels_clamped(self):
        """Out-of-range channels are clamped to [0,255]."""
        self.assertEqual(self._color_body([300, -5, 0]),
                         {"r": 255, "g": 0, "b": 0})

    def test_v1_request_shape(self):
        """Color issues a PUT to {v1_base_url}/v1/devices/control with the
        {device, model, cmd:{name:'color', value:{r,g,b}}} body and the
        Govee-API-Key header, using raw (not packed) channels."""
        govee = make_govee([FakeResponse(200, v1_success_body())])
        govee.set_device_color(GoveeDevice("H6160", "AA:BB"), [18, 52, 86])

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_V1_BASE_URL + GOVEE_V1_PATH_CONTROL)
        # The v1 body identifies the device by MAC + model (sku) at the top
        # level, and carries the raw-channel color command.
        body = call["json"]
        self.assertEqual(body["device"], "AA:BB")
        self.assertEqual(body["model"], "H6160")
        self.assertEqual(body["cmd"]["name"], "color")
        self.assertEqual(body["cmd"]["value"], {"r": 18, "g": 52, "b": 86})
        # There must be no v2 capability/packed-int structure on this path.
        self.assertNotIn("payload", body)
        self.assertNotIn("capability", body)
        # The authentication header is present on the request.
        self.assertEqual(call["headers"].get(GOVEE_API_KEY_HEADER),
                         TEST_API_KEY)

    def test_uses_configured_v1_base_url(self):
        """The color request honors an overridden v1_base_url."""
        govee = make_govee([FakeResponse(200, v1_success_body())],
                           v1_base_url="https://v1.proxy.local")
        govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(govee.session.calls[0]["url"],
                         "https://v1.proxy.local" + GOVEE_V1_PATH_CONTROL)

    def test_v1_success_validation(self):
        """A v1 success body ({code:200, message:'Success'}) validates and is
        returned."""
        govee = make_govee([FakeResponse(200, v1_success_body())])
        body = govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["message"], "Success")

    def test_v1_failure_raises(self):
        """A v1 failure (non-200 body code) raises GoveeError."""
        govee = make_govee([
            FakeResponse(200, {"code": 400, "message": "Invalid value"}),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError) as ctx:
            govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(ctx.exception.code, 400)

    def test_v1_http_failure_raises(self):
        """A v1 HTTP error (e.g. 401 bad key) raises GoveeError."""
        govee = make_govee([
            FakeResponse(401, {"code": 401, "message": "Unauthorized"}),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError) as ctx:
            govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(ctx.exception.http_status, 401)


class TestColorPackedHelper(unittest.TestCase):
    """Tests for the retained packed-int helper `_color_to_api` (not used by
    the active v1 color path, but kept for reference/compatibility)."""

    def test_red(self):
        self.assertEqual(Govee._color_to_api([255, 0, 0]), 16711680)

    def test_green(self):
        self.assertEqual(Govee._color_to_api([0, 255, 0]), 65280)

    def test_blue(self):
        self.assertEqual(Govee._color_to_api([0, 0, 255]), 255)

    def test_white(self):
        self.assertEqual(Govee._color_to_api([255, 255, 255]), 16777215)

    def test_channels_clamped(self):
        self.assertEqual(Govee._color_to_api([300, -5, 0]), 16711680)


# ======================= API-version selection tests ====================== #
class TestNormalizeApiVersion(unittest.TestCase):
    """Tests for the shared `normalize_api_version` validator."""

    def test_valid_v1(self):
        self.assertEqual(
            normalize_api_version("v1", GOVEE_API_VERSION_V2),
            GOVEE_API_VERSION_V1)

    def test_valid_v2(self):
        self.assertEqual(
            normalize_api_version("v2", GOVEE_API_VERSION_V1),
            GOVEE_API_VERSION_V2)

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(
            normalize_api_version("  V1 ", GOVEE_API_VERSION_V2),
            GOVEE_API_VERSION_V1)

    def test_none_falls_back_to_default(self):
        self.assertEqual(
            normalize_api_version(None, GOVEE_API_VERSION_V2),
            GOVEE_API_VERSION_V2)

    def test_invalid_string_falls_back_to_default(self):
        self.assertEqual(
            normalize_api_version("v3", GOVEE_API_VERSION_V1),
            GOVEE_API_VERSION_V1)

    def test_non_string_falls_back_to_default(self):
        self.assertEqual(
            normalize_api_version(2, GOVEE_API_VERSION_V1),
            GOVEE_API_VERSION_V1)


class TestDeviceApiVersionDefaults(unittest.TestCase):
    """Tests that `GoveeDevice` parses/defaults its per-device API prefs."""

    def test_defaults(self):
        """A bare device gets color=v1, brightness=v2 by default."""
        d = GoveeDevice("H6160", "AA")
        self.assertEqual(d.color_api, GOVEE_DEFAULT_COLOR_API)
        self.assertEqual(d.brightness_api, GOVEE_DEFAULT_BRIGHTNESS_API)
        self.assertEqual(d.color_api, GOVEE_API_VERSION_V1)
        self.assertEqual(d.brightness_api, GOVEE_API_VERSION_V2)

    def test_explicit_values(self):
        """Explicit valid values are honored."""
        d = GoveeDevice("H6160", "AA", color_api="v2", brightness_api="v1")
        self.assertEqual(d.color_api, GOVEE_API_VERSION_V2)
        self.assertEqual(d.brightness_api, GOVEE_API_VERSION_V1)

    def test_invalid_values_fall_back_to_defaults(self):
        """Invalid values fall back to the per-attribute defaults."""
        d = GoveeDevice("H6160", "AA", color_api="bogus",
                        brightness_api="also-bad")
        self.assertEqual(d.color_api, GOVEE_DEFAULT_COLOR_API)
        self.assertEqual(d.brightness_api, GOVEE_DEFAULT_BRIGHTNESS_API)


class TestColorApiDispatch(unittest.TestCase):
    """Tests that `set_device_color` dispatches on the device's `color_api`."""

    def test_v1_device_uses_v1_raw_channel_shape(self):
        """A v1-configured device sends the v1 {r,g,b} control body."""
        govee = make_govee([FakeResponse(200, v1_success_body())])
        device = GoveeDevice("H6160", "AA:BB", color_api="v1")
        govee.set_device_color(device, [18, 52, 86])

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_V1_BASE_URL + GOVEE_V1_PATH_CONTROL)
        body = call["json"]
        self.assertEqual(body["device"], "AA:BB")
        self.assertEqual(body["model"], "H6160")
        self.assertEqual(body["cmd"]["name"], GOVEE_V1_COLOR_CMD_NAME)
        self.assertEqual(body["cmd"]["value"], {"r": 18, "g": 52, "b": 86})
        self.assertNotIn("capability", body)

    def test_v2_device_uses_packed_int_capability(self):
        """A v2-configured device sends the v2 packed-int colorRgb capability."""
        govee = make_govee([FakeResponse(200, success_body())])
        device = GoveeDevice("H6160", "AA:BB", color_api="v2")
        govee.set_device_color(device, [18, 52, 86])

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], GOVEE_DEFAULT_BASE_URL + GOVEE_PATH_CONTROL)
        cap = call["json"]["payload"]["capability"]
        self.assertEqual(cap["type"], "devices.capabilities.color_setting")
        self.assertEqual(cap["instance"], "colorRgb")
        # (18 << 16) | (52 << 8) | 86 == 1191510
        self.assertEqual(cap["value"], (18 << 16) | (52 << 8) | 86)

    def test_v2_device_clamps_channels(self):
        """Out-of-range channels are clamped before packing on the v2 path."""
        govee = make_govee([FakeResponse(200, success_body())])
        device = GoveeDevice("H6160", "AA", color_api="v2")
        govee.set_device_color(device, [300, -5, 0])
        cap = govee.session.calls[0]["json"]["payload"]["capability"]
        # [255, 0, 0] packed == 16711680
        self.assertEqual(cap["value"], 16711680)

    def test_default_device_uses_v1_color(self):
        """A device without an explicit color_api defaults to v1 color."""
        govee = make_govee([FakeResponse(200, v1_success_body())])
        govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(govee.session.calls[0]["method"], "PUT")


class TestBrightnessApiDispatch(unittest.TestCase):
    """Tests that `set_device_brightness` dispatches on `brightness_api`."""

    def _v1_brightness_value(self, brightness):
        govee = make_govee([FakeResponse(200, v1_success_body())])
        device = GoveeDevice("H6160", "AA", brightness_api="v1")
        govee.set_device_brightness(device, brightness)
        return govee.session.calls[0]

    def test_v2_device_uses_range_brightness(self):
        """A v2-configured device sends the range/brightness capability."""
        govee = make_govee([FakeResponse(200, success_body())])
        device = GoveeDevice("H6160", "AA", brightness_api="v2")
        govee.set_device_brightness(device, 0.5)

        call = govee.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], GOVEE_DEFAULT_BASE_URL + GOVEE_PATH_CONTROL)
        cap = call["json"]["payload"]["capability"]
        self.assertEqual(cap["type"], "devices.capabilities.range")
        self.assertEqual(cap["instance"], "brightness")
        self.assertEqual(cap["value"], 50)

    def test_default_device_uses_v2_brightness(self):
        """A device without an explicit brightness_api defaults to v2."""
        govee = make_govee([FakeResponse(200, success_body())])
        govee.set_device_brightness(GoveeDevice("H6160", "AA"), 0.5)
        self.assertEqual(govee.session.calls[0]["method"], "POST")

    def test_v1_device_uses_v1_brightness_command(self):
        """A v1-configured device sends the v1 brightness control body."""
        call = self._v1_brightness_value(0.5)
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_V1_BASE_URL + GOVEE_V1_PATH_CONTROL)
        body = call["json"]
        self.assertEqual(body["device"], "AA")
        self.assertEqual(body["model"], "H6160")
        self.assertEqual(body["cmd"]["name"], GOVEE_V1_BRIGHTNESS_CMD_NAME)
        self.assertEqual(body["cmd"]["value"], 50)
        self.assertNotIn("capability", body)

    def test_v1_full_brightness(self):
        """1.0 maps to 100 on the v1 path."""
        self.assertEqual(self._v1_brightness_value(1.0)["json"]["cmd"]["value"],
                         100)

    def test_v1_zero_maps_to_zero(self):
        """0.0 maps to 0 on the v1 path (v1 minimum is 0, unlike v2's 1)."""
        self.assertEqual(self._v1_brightness_value(0.0)["json"]["cmd"]["value"],
                         0)

    def test_v1_over_range_clamps_to_max(self):
        """Values above 1.0 clamp to 100 on the v1 path."""
        self.assertEqual(self._v1_brightness_value(1.5)["json"]["cmd"]["value"],
                         100)

    def test_v1_negative_clamps_to_min(self):
        """Negative floats clamp to 0 on the v1 path."""
        self.assertEqual(
            self._v1_brightness_value(-0.5)["json"]["cmd"]["value"], 0)

    def test_v1_half_brightness(self):
        """0.5 maps to 50 on the v1 path."""
        self.assertEqual(self._v1_brightness_value(0.5)["json"]["cmd"]["value"],
                         50)


class TestTogglePlug(unittest.TestCase):
    """Tests for the `toggle_plug` convenience method."""

    def test_toggle_on(self):
        govee = make_govee([FakeResponse(200, success_body())])
        govee.toggle_plug(GoveeDevice("H5083", "AA"), True)
        cap = govee.session.calls[0]["json"]["payload"]["capability"]
        self.assertEqual(cap["value"], 1)

    def test_toggle_off(self):
        govee = make_govee([FakeResponse(200, success_body())])
        govee.toggle_plug(GoveeDevice("H5083", "AA"), False)
        cap = govee.session.calls[0]["json"]["payload"]["capability"]
        self.assertEqual(cap["value"], 0)


# =========================== Validation Tests ============================== #
class TestValidation(unittest.TestCase):
    """Tests for HTTP-status and body-`code` validation."""

    def test_success_http_and_code(self):
        """HTTP 200 + body code 200 succeeds."""
        govee = make_govee([FakeResponse(200, success_body())])
        body = govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(body["code"], 200)

    def test_body_code_failure_raises(self):
        """HTTP 200 but body code 400 raises GoveeError with that code."""
        govee = make_govee([
            FakeResponse(200, {"code": 400, "message": "bad value"}),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError) as ctx:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(ctx.exception.code, 400)

    def test_http_401_raises(self):
        """HTTP 401 (bad key) raises GoveeError carrying the status."""
        govee = make_govee([
            FakeResponse(401, {"code": 401, "message": "unauthorized"}),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError) as ctx:
            govee.get_devices()
        self.assertEqual(ctx.exception.http_status, 401)

    def test_http_404_raises(self):
        """HTTP 404 (device not found) raises GoveeError."""
        govee = make_govee([
            FakeResponse(404, {"code": 404, "message": "not found"}),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError) as ctx:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(ctx.exception.http_status, 404)

    def test_malformed_body_raises(self):
        """HTTP 200 with a non-JSON body raises GoveeError."""
        govee = make_govee([
            FakeResponse(200, None, raise_json=True),
        ], retry_attempts=1)
        with self.assertRaises(GoveeError):
            govee.get_devices()


# ========================= Retry / Backoff Tests =========================== #
class TestRetry(unittest.TestCase):
    """Tests for the retry loop and 429 rate-limit backoff."""

    def test_retry_on_5xx_then_success(self):
        """A 5xx error is retried and a subsequent 200 succeeds."""
        govee = make_govee([
            FakeResponse(500, {"code": 500, "message": "server error"}),
            FakeResponse(200, success_body()),
        ], retry_attempts=4)
        with mock.patch("time.sleep") as slept:
            body = govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(body["code"], 200)
        # One retry sleep should have occurred.
        self.assertEqual(slept.call_count, 1)
        self.assertEqual(len(govee.session.calls), 2)

    def test_retry_exhausted_raises(self):
        """Repeated failures exhaust attempts and raise GoveeError."""
        govee = make_govee([
            FakeResponse(500, {"code": 500}),
            FakeResponse(500, {"code": 500}),
            FakeResponse(500, {"code": 500}),
        ], retry_attempts=3)
        # Capture the fake session up front: on exhaustion `handle_error`
        # refreshes (replaces) the session, so read the original here.
        session = govee.session
        with mock.patch("time.sleep") as slept:
            with self.assertRaises(GoveeError):
                govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        # Slept between each of the 3 attempts.
        self.assertEqual(slept.call_count, 3)
        self.assertEqual(len(session.calls), 3)

    def test_transport_error_retried(self):
        """A requests transport error is retried like other failures.

        A transport failure triggers a session `refresh()` mid-loop; we patch
        it to a no-op so the fake session (and its queued success response)
        remains in place for the retry.
        """
        govee = make_govee([
            requests.ConnectionError("boom"),
            FakeResponse(200, success_body()),
        ], retry_attempts=4)
        with mock.patch("time.sleep"), \
             mock.patch.object(govee, "refresh"):
            body = govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(body["code"], 200)

    def test_429_backoff_honors_reset_header(self):
        """A 429 with a Reset header backs off until (near) the reset time."""
        # Reset header points ~5 seconds into the future.
        reset_epoch = 1000.0
        headers = {"API-RateLimit-Reset": str(reset_epoch)}
        govee = make_govee([
            FakeResponse(429, {"message": "too many"}, headers=headers),
            FakeResponse(200, success_body()),
        ], retry_attempts=4)

        with mock.patch("time.time", return_value=reset_epoch - 5), \
             mock.patch("time.sleep") as slept:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")

        # Slept once for the backoff, and honored ~5 seconds (capped).
        self.assertEqual(slept.call_count, 1)
        slept_seconds = slept.call_args[0][0]
        self.assertAlmostEqual(slept_seconds, 5, delta=0.5)

    def test_429_backoff_caps_reset(self):
        """A huge Reset delta is capped at the max backoff."""
        headers = {"API-RateLimit-Reset": "9999999999"}
        govee = make_govee([
            FakeResponse(429, {"message": "too many"}, headers=headers),
            FakeResponse(200, success_body()),
        ], retry_attempts=4)
        with mock.patch("time.time", return_value=0.0), \
             mock.patch("time.sleep") as slept:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertLessEqual(slept.call_args[0][0], 60)

    def test_429_without_headers_uses_retry_delay(self):
        """A 429 without headers falls back to the configured retry delay."""
        govee = make_govee([
            FakeResponse(429, {"message": "too many"}),
            FakeResponse(200, success_body()),
        ], retry_attempts=4, retry_delay=2)
        with mock.patch("time.sleep") as slept:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(slept.call_args[0][0], 2)

    def test_429_exhausted_raises(self):
        """Persistent 429s exhaust attempts and raise a GoveeError."""
        govee = make_govee([
            FakeResponse(429, {"message": "too many"}),
            FakeResponse(429, {"message": "too many"}),
        ], retry_attempts=2)
        with mock.patch("time.sleep"):
            with self.assertRaises(GoveeError) as ctx:
                govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(ctx.exception.http_status, 429)


# ===================== Device-Busy / Rate-Limit Body ======================= #
class TestDeviceBusyRetry(unittest.TestCase):
    """Tests that per-device rate-limit / device-busy responses (including the
    common HTTP-200-envelope form) are retried with backoff.
    """

    def test_busy_body_message_then_success(self):
        """A 200 envelope reporting 'device is busy' is retried, then succeeds."""
        govee = make_govee([
            FakeResponse(200, {"code": 429, "message": "device is busy"}),
            FakeResponse(200, v1_success_body()),
        ], retry_attempts=4)
        with mock.patch("time.sleep") as slept:
            body = govee.set_device_color(GoveeDevice("H6160", "AA"),
                                          [10, 20, 30])
        self.assertEqual(body["code"], 200)
        # One backoff sleep for the busy response, then the retry succeeded.
        self.assertEqual(slept.call_count, 1)
        self.assertEqual(len(govee.session.calls), 2)

    def test_busy_body_code_then_success(self):
        """A rate-limit body `code` (429 inside a 200) triggers a retry."""
        govee = make_govee([
            FakeResponse(200, {"code": 429, "message": "too many requests"}),
            FakeResponse(200, success_body()),
        ], retry_attempts=4)
        with mock.patch("time.sleep"):
            body = govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(body["code"], 200)
        self.assertEqual(len(govee.session.calls), 2)

    def test_persistent_busy_surfaces_govee_error(self):
        """Persistent device-busy responses exhaust attempts and raise."""
        govee = make_govee([
            FakeResponse(200, {"code": 429, "message": "device is busy"}),
            FakeResponse(200, {"code": 429, "message": "device is busy"}),
        ], retry_attempts=2)
        with mock.patch("time.sleep"):
            with self.assertRaises(GoveeError):
                govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])

    def test_non_busy_body_failure_not_treated_as_rate_limit(self):
        """A genuine failure (bad value) is not treated as device-busy: it is
        retried as an application error and surfaces its body code.
        """
        govee = make_govee([
            FakeResponse(200, {"code": 400, "message": "bad value"}),
        ], retry_attempts=1)
        with mock.patch("time.sleep"):
            with self.assertRaises(GoveeError) as ctx:
                govee.set_device_color(GoveeDevice("H6160", "AA"), [1, 2, 3])
        self.assertEqual(ctx.exception.code, 400)


# ===================== Color Regression Guard ============================== #
class TestColorRegressionGuard(unittest.TestCase):
    """Explicit regression guard that a color control call sends the correct
    v1 request: a PUT to `{v1_base_url}/v1/devices/control` carrying
    `{device, model, cmd:{name:'color', value:{r,g,b}}}` with raw (clamped)
    channels.

    Guards against a regression back to the (physically no-op on H6160) v2
    `colorRgb` packed-int capability path.
    """

    def test_color_v1_request_and_body(self):
        govee = make_govee([FakeResponse(200, v1_success_body())])
        govee.set_device_color(GoveeDevice("H6160", "AA:BB"), [18, 52, 86])
        call = govee.session.calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["url"],
                         GOVEE_DEFAULT_V1_BASE_URL + GOVEE_V1_PATH_CONTROL)
        body = call["json"]
        self.assertEqual(body["device"], "AA:BB")
        self.assertEqual(body["model"], "H6160")
        self.assertEqual(body["cmd"], {
            "name": "color",
            "value": {"r": 18, "g": 52, "b": 86},
        })


# ===================== Inter-Command Delay (spacing) ======================= #
class TestCommandDelay(unittest.TestCase):
    """Tests that successive control calls to the same device are spaced by
    `command_delay`, asserted via a MOCKED sleep (no real delay).
    """

    def test_delay_applied_between_same_device_commands(self):
        """A second control call to the same device sleeps up to command_delay."""
        govee = make_govee([
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
        ], command_delay=0.75)
        device = GoveeDevice("H6160", "AA")

        # Drive monotonic time deterministically: each call to time.monotonic
        # advances 0 seconds so the full command_delay is owed each time.
        with mock.patch("time.monotonic", return_value=100.0), \
             mock.patch("time.sleep") as slept:
            govee.set_device_power(device, "on")     # first: no spacing
            govee.set_device_color(device, [1, 2, 3])  # second: spaced
            govee.set_device_brightness(device, 0.5)   # third: spaced

        # First command has no prior timestamp -> no sleep. The next two each
        # owe the full delay because monotonic time did not advance.
        self.assertEqual(slept.call_count, 2)
        for call in slept.call_args_list:
            self.assertAlmostEqual(call[0][0], 0.75, delta=1e-6)
        self.assertEqual(len(govee.session.calls), 3)

    def test_no_delay_when_disabled(self):
        """command_delay=0 disables spacing entirely (no sleeps)."""
        govee = make_govee([
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
        ], command_delay=0)
        device = GoveeDevice("H6160", "AA")
        with mock.patch("time.sleep") as slept:
            govee.set_device_power(device, "on")
            govee.set_device_color(device, [1, 2, 3])
        self.assertEqual(slept.call_count, 0)

    def test_no_delay_for_first_command_to_device(self):
        """The very first command to a device is not delayed."""
        govee = make_govee([FakeResponse(200, success_body())],
                           command_delay=1.0)
        with mock.patch("time.sleep") as slept:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
        self.assertEqual(slept.call_count, 0)

    def test_delay_not_applied_across_different_devices(self):
        """Spacing is per-device: a command to a different device is not
        delayed by a prior command to another device."""
        govee = make_govee([
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
        ], command_delay=1.0)
        with mock.patch("time.monotonic", return_value=100.0), \
             mock.patch("time.sleep") as slept:
            govee.set_device_power(GoveeDevice("H6160", "AA"), "on")
            govee.set_device_power(GoveeDevice("H5083", "BB"), "on")
        self.assertEqual(slept.call_count, 0)

    def test_elapsed_time_reduces_owed_delay(self):
        """If enough time has already elapsed, no (or reduced) sleep occurs."""
        govee = make_govee([
            FakeResponse(200, success_body()),
            FakeResponse(200, success_body()),
        ], command_delay=1.0)
        device = GoveeDevice("H6160", "AA")
        # First command records t=100; second sees t=100.6 -> owes 0.4s.
        # monotonic() is called: record#1 (100.0), apply#2 (100.6),
        # record#2 (101.0).
        times = iter([100.0, 100.6, 101.0])
        with mock.patch("time.monotonic", side_effect=lambda: next(times)), \
             mock.patch("time.sleep") as slept:
            govee.set_device_power(device, "on")
            govee.set_device_color(device, [1, 2, 3])
        self.assertEqual(slept.call_count, 1)
        self.assertAlmostEqual(slept.call_args[0][0], 0.4, delta=1e-6)


# ============================= Caching Tests =============================== #
class TestCaching(unittest.TestCase):
    """Tests for device-list caching and refresh behavior."""

    def test_second_call_uses_cache(self):
        """Two calls within refresh_delay perform only one HTTP request."""
        govee = make_govee([
            FakeResponse(200, success_body(data=[
                {"sku": "H6160", "device": "AA", "deviceName": "Strip"},
            ])),
        ], refresh_delay=7200)
        govee.get_devices()
        govee.get_devices()
        self.assertEqual(len(govee.session.calls), 1)

    def test_refresh_true_refetches(self):
        """`refresh=True` forces a new HTTP request."""
        govee = make_govee([
            FakeResponse(200, success_body(data=[
                {"sku": "H6160", "device": "AA"},
            ])),
            FakeResponse(200, success_body(data=[
                {"sku": "H6160", "device": "AA"},
                {"sku": "H5083", "device": "BB"},
            ])),
        ])
        first = govee.get_devices()
        second = govee.get_devices(refresh=True)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(govee.session.calls), 2)

    def test_expired_ttl_refetches(self):
        """An elapsed refresh_delay triggers a refetch."""
        govee = make_govee([
            FakeResponse(200, success_body(data=[{"sku": "H6160",
                                                  "device": "AA"}])),
            FakeResponse(200, success_body(data=[{"sku": "H6160",
                                                  "device": "AA"}])),
        ], refresh_delay=0)
        govee.get_devices()
        govee.get_devices()
        # With a zero TTL, the second call must refetch.
        self.assertEqual(len(govee.session.calls), 2)


# ============================= Lookup Tests ================================ #
class TestLookups(unittest.TestCase):
    """Tests for name/address device lookups."""

    def _seed(self):
        return make_govee([
            FakeResponse(200, success_body(data=[
                {"sku": "H6160", "device": "AA:11", "deviceName": "Staircase"},
                {"sku": "H5083", "device": "BB:22", "deviceName": "Stereo"},
            ])),
        ])

    def test_get_device_by_name_match(self):
        """A name match returns the correct cached device."""
        govee = self._seed()
        d = govee.get_device_by_name("Staircase")
        self.assertIsNotNone(d)
        self.assertEqual(d.mac, "AA:11")

    def test_get_device_by_name_no_match(self):
        """A non-matching name returns None."""
        govee = self._seed()
        self.assertIsNone(govee.get_device_by_name("Nonexistent"))

    def test_get_device_by_address_cached(self):
        """An address lookup returns the cached device (with metadata)."""
        govee = self._seed()
        d = govee.get_device_by_address("BB:22", "H5083")
        self.assertEqual(d.name, "Stereo")

    def test_get_device_by_address_builds_minimal(self):
        """An uncached address yields a minimal constructed device."""
        govee = self._seed()
        d = govee.get_device_by_address("CC:33", "H6160")
        self.assertEqual(d.mac, "CC:33")
        self.assertEqual(d.sku, "H6160")
        self.assertIsNone(d.name)


# ========================== Error / Session Tests ========================== #
class TestErrorHandling(unittest.TestCase):
    """Tests that error handling resets the session."""

    def test_session_reset_after_transport_failure(self):
        """Transport failures invoke `refresh()` to reset the session.

        `refresh` is patched to a no-op so the test never touches the real
        network (a real session would otherwise be installed mid-loop); we
        assert it was invoked, which is the session-reset mechanism, and that
        the underlying error propagates.
        """
        govee = make_govee([
            requests.ConnectionError("boom"),
            requests.ConnectionError("boom"),
        ], retry_attempts=2)
        with mock.patch("time.sleep"), \
             mock.patch.object(govee, "refresh") as refreshed:
            with self.assertRaises(requests.ConnectionError):
                govee.get_devices()
        # refresh is called on each transport error and again in handle_error.
        self.assertTrue(refreshed.called)


if __name__ == "__main__":
    unittest.main()
