# Lumen — Smart Home Lighting

Lumen controls WiFi-connected lights and smart plugs through multiple backends.

## Purpose

* Manage and control smart home lighting devices
* Support multiple toggle backends: IFTTT webhooks, Wyze SDK, and LIFX LAN protocol
* Expose NLA endpoints for natural-language device control
* Provide HTTP endpoints for programmatic light control

## Architecture

Lumen maintains an async action queue with a configurable pool of worker threads. When a toggle request arrives, it's placed on the queue and processed by the next available `LumenThread`. Per-light locks prevent concurrent actions on the same device.

The service supports three toggle backends:

| Backend | Protocol | Use Case |
|---------|----------|----------|
| **IFTTT** | Cloud webhooks | Lights controlled via IFTTT applets |
| **Wyze** | Cloud SDK | Wyze smart plugs |
| **LIFX** | LAN protocol | LIFX bulbs on the local network |

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
| `dialogue` | `DialogueConfig` | OpenAI settings for NLA text resolution |
| `refresh_rate` | `int` | Service tick interval |
| `action_threads` | `int` | Number of action worker threads |
| `nla_toggle_dialogue_retries` | `int` | LLM retries for NLA device matching |

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
* All other lights fall back to the IFTTT webhook backend

## Dependencies

* **Library modules:** `lib.ifttt`, `lib.wyze`, `lib.lifx`, `lib.dialogue`, `lib.oracle`, `lib.service`
* **External services:** IFTTT (cloud), Wyze (cloud), LIFX (LAN)
* **Other services:** None (Lumen is called by others, not the other way around)

## Notable Details

* Tag-based device matching allows grouping lights (e.g., all "bedroom" lights)
* Per-light locks ensure thread-safe concurrent control
* The LIFX backend uses LAN discovery with configurable retry attempts and delays
* Gatekeeper's motion-detection subscribers call Lumen to turn on outdoor lights
