# Lumen — Smart Home Lighting

Lumen controls WiFi-connected lights and smart plugs through multiple backends.

## Purpose

* Manage and control smart home lighting devices
* Support multiple toggle backends: IFTTT webhooks, Wyze SDK, LIFX LAN protocol, and the Govee Developer API
* Expose NLA endpoints for natural-language device control
* Provide HTTP endpoints for programmatic light control

## Architecture

Lumen maintains an async action queue with a configurable pool of worker threads. When a toggle request arrives, it's placed on the queue and processed by the next available `LumenThread`. Per-light locks prevent concurrent actions on the same device.

The service supports four toggle backends:

| Backend | Protocol | Use Case |
|---------|----------|----------|
| **IFTTT** | Cloud webhooks | Lights controlled via IFTTT applets (also the fallback) |
| **Wyze** | Cloud SDK | Wyze smart plugs |
| **LIFX** | LAN protocol | LIFX bulbs on the local network |
| **Govee** | Cloud API v2 | Govee lights and plugs (see [Govee integration](govee-integration.md)) |

Each light in the config specifies which backend it uses.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/lights` | List all configured lights and their current statuses |
| `POST` | `/toggle` | Toggle a light on or off |

### `/toggle` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Light identifier |
| `action` | Yes | `"on"` or `"off"` |
| `color` | No | RGB color as `"r,g,b"` string (requires `has_color: true`) |
| `brightness` | No | Brightness level (requires `has_brightness: true`) |

## NLA Endpoints

| Name | Description |
|------|-------------|
| `get_devices` | Retrieve information about devices that Lumen can control |
| `toggle_device` | Toggle a device on/off, or set its color/brightness |

The `toggle_device` NLA endpoint uses the LLM to resolve natural-language requests (e.g., "turn on the kitchen lights") to specific device names and actions, with configurable retry attempts.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `lights` | `list[LightConfig]` | Light/device definitions |
| `webhook_event` | `str` | IFTTT webhook event name |
| `webhook_key` | `str` | IFTTT webhook API key |
| `wyze_config` | `WyzeConfig` | Wyze account credentials and API keys |
| `lifx_config` | `LIFXConfig` | LIFX LAN settings (optional) |
| `govee_config` | `GoveeConfig` | Govee Developer API v2 settings (optional; see [Govee integration](govee-integration.md)) |
| `dialogue` | `DialogueConfig` | OpenAI settings for NLA text resolution |
| `refresh_rate` | `int` | Service tick interval |
| `action_threads` | `int` | Number of action worker threads |
| `nla_toggle_dialogue_retries` | `int` | LLM retries for NLA device matching |

### LIFX reliability settings

The `lifx_config` (`LIFXConfig`) controls LIFX LAN reliability behavior:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `refresh_delay` | `int` | `7200` | Seconds before the discovery cache is stale |
| `retry_attempts` | `int` | `4` | Retry attempts for discovery and power commands |
| `retry_delay` | `int`/`float` | `0.1` | Seconds slept between retries |
| `command_delay` | `int`/`float` | `0.05` | Seconds to space successive LAN commands across all bulbs so a burst of per-bulb toggles is not sent at once (`0` disables the stagger) |

LIFX power commands are **acknowledged and verified** (not fire-and-forget): a
lost command is retried, the resulting power state is read back and confirmed,
and a bulb that cannot be confirmed on/off after its retries raises an error so
Lumen logs a real failure (with the device name) instead of a false "turned on".
A cache miss in `get_light_by_name` triggers exactly one fresh re-discovery
before giving up. See the [library docs](../library.md) (`lifx.py`) for details.

#### Thread-safe LIFX LAN access (serialized)

Lumen runs several action worker threads (`action_threads`, default `8`) that
all share **one** `LIFX` wrapper and its cached `Light`/`Device` objects. The
underlying `lifxlan` library manages its UDP sockets through a process-global,
**un-locked** socket table, so when multiple threads opened/closed those sockets
concurrently — or one thread rebuilt the shared `LifxLAN` mid-flight — sockets
were torn down out from under in-flight commands, producing intermittent
`[Errno 9] Bad file descriptor` failures (e.g. only 1 of 5 kitchen bulbs turning
on). The earlier per-command `command_delay` stagger only spaced command *starts*
and did not prevent overlap, so it could not fix this.

All LIFX LAN operations are now **serialized** by a single reentrant lock inside
the `LIFX` wrapper: discovery, lookups, and each power/color/brightness command
(including its acknowledged send + read-back verification) hold the lock for
their full duration, so no two threads ever touch `lifxlan` sockets at the same
time. The shared `LifxLAN` is **no longer rebuilt on a transient command retry**
(a lost ack is retried on the same object); it is only refreshed for genuine
discovery problems, always under the lock. Because LAN calls are fast,
serializing a handful of bulbs is imperceptible to the user. This supersedes the
pure-stagger approach as the real fix; `command_delay` is retained as a small,
optional spacing knob. See the [library docs](../library.md) (`lifx.py`).

### Light Configuration

Each light is defined with:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique light identifier |
| `description` | `str` | Human-readable description |
| `has_color` | `bool` | Whether the light supports color control |
| `has_brightness` | `bool` | Whether the light supports brightness control |
| `tags` | `list[str]` | Tags for grouping and matching |

The toggle backend for each light is determined by its tags:

* Lights tagged `"wyze"` are controlled via the Wyze SDK
* Lights tagged `"lifx"` are controlled via the LIFX LAN protocol
* Lights tagged `"govee"` are controlled via the Govee Developer API (when a `govee_config` is present; otherwise they fall back to IFTTT)
* All other lights fall back to the IFTTT webhook backend

## Dependencies

* **Library modules:** `lib.ifttt`, `lib.wyze`, `lib.lifx`, `lib.govee`, `lib.dialogue`, `lib.oracle`, `lib.service`
* **External services:** IFTTT (cloud), Wyze (cloud), LIFX (LAN), Govee (cloud API v2)
* **Other services:** None (Lumen is called by others, not the other way around)

## Notable Details

* Tag-based device matching allows grouping lights (e.g., all "bedroom" lights)
* Per-light locks ensure thread-safe concurrent control (always released, even when a backend command fails)
* The LIFX backend uses LAN discovery with configurable retry attempts and delays
* LIFX power commands are acknowledged, retried, and verified (read-back); real success/failure is logged per device
* All LIFX LAN access is serialized by a reentrant lock in the shared `LIFX` wrapper, so concurrent worker threads cannot race on `lifxlan`'s un-locked sockets (fixes intermittent `[Errno 9] Bad file descriptor`); the shared `LifxLAN` is never rebuilt on a transient command retry
