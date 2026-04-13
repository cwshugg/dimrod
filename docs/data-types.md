# Data Types

This document describes the core data modeling framework and common data types used across DImROD services.

## The Uniserdes Framework

**Uniserdes** (Universal Serializer/Deserializer) is the foundational data class for all models in DImROD. Every configuration object, API payload, database record, and domain model inherits from `Uniserdes`.

### Defining a Model

To define a model, subclass `Uniserdes` and declare a `fields` list of `UniserdesField` objects:

```python
from lib.uniserdes import Uniserdes, UniserdesField

class Recipe(Uniserdes):
    fields = [
        UniserdesField("title",       [str],   required=True),
        UniserdesField("servings",    [int],   required=True),
        UniserdesField("description", [str],   required=False, default=""),
        UniserdesField("is_favorite", [bool],  required=False, default=False),
    ]
```

Each `UniserdesField` has:

| Parameter | Description |
|-----------|-------------|
| `name` | The field name (used as both the attribute name and JSON key) |
| `types` | A list of accepted Python types for the field |
| `required` | Whether the field must be present when parsing |
| `default` | Default value if the field is absent |

### Supported Field Types

Uniserdes handles the following types automatically during serialization and deserialization:

| Type | JSON Representation |
|------|-------------------|
| `str` | String |
| `int` | Number |
| `float` | Number |
| `bool` | Boolean |
| `dict` | Object |
| `list` | Array |
| `datetime` | ISO 8601 string |
| `Enum` | Integer value |
| Nested `Uniserdes` | Nested JSON object |
| List of `Uniserdes` | Array of JSON objects |

### Serialization and Deserialization

Uniserdes provides multiple serialization formats:

**JSON (primary):**

```python
# Parse from JSON dict
obj = MyModel()
obj.parse_json(json_dict)

# Parse from file
obj = MyModel()
obj.parse_file("/path/to/file.json")

# Class methods for construction
obj = MyModel.from_json(json_dict)
obj = MyModel.from_file("/path/to/file.json")

# Serialize to JSON dict
data = obj.to_json()
```

**Bytes and Hex:**

```python
raw = obj.to_bytes()       # JSON -> UTF-8 bytes
obj.parse_bytes(raw)       # UTF-8 bytes -> parse

hex_str = obj.to_hex()     # JSON -> hex string
obj.parse_hex(hex_str)     # hex string -> parse
```

**CSV:**

```python
csv_str = obj.to_csv()     # Comma-separated field values
```

**SQLite3:**

```python
# Generate a CREATE TABLE statement
ddl = MyModel.get_sqlite3_table_definition("my_table")

# Convert to a SQLite3-compatible tuple
row = obj.to_sqlite3()
row_str = obj.to_sqlite3_str()

# Parse from a SQLite3 row tuple
obj = MyModel.from_sqlite3(row_tuple)
```

### Nested Objects

Uniserdes handles nested objects automatically. If a field's type is another `Uniserdes` subclass, it is recursively serialized/deserialized:

```python
class Ingredient(Uniserdes):
    fields = [
        UniserdesField("title", [str], required=True),
        UniserdesField("quantity", [float], required=True),
    ]

class Recipe(Uniserdes):
    fields = [
        UniserdesField("title", [str], required=True),
        UniserdesField("ingredients", [Ingredient], required=True),  # list of nested objects
    ]
```

### Extra Fields

Any JSON keys that do not match a declared field are stored in the `extra_fields` dictionary. This allows models to carry data they don't explicitly define.

### Copying

```python
clone = obj.copy()  # Deep copy via JSON round-trip
```

## Config Types

The `Config` and `ConfigField` classes are thin aliases over `Uniserdes` and `UniserdesField`, used to semantically distinguish configuration objects from other data models:

```python
from lib.config import Config, ConfigField

class MyServiceConfig(Config):
    fields = [
        ConfigField("api_key", [str], required=True),
        ConfigField("refresh_rate", [int], required=False, default=60),
    ]
```

### ServiceConfig

The base config for all services. Every service config inherits from or includes these fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `service_name` | `str` | Yes | Unique identifier for the service |
| `service_log` | `str` | No | Log destination (default: `"stdout"`) |
| `msghub_name` | `str` | Yes | ntfy.sh topic for push notifications |
| `oracle` | `OracleConfig` | Yes | HTTP API server configuration |

### OracleConfig

Configuration for the HTTP API server:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `addr` | `str` | Yes | Bind address |
| `port` | `int` | Yes | Listen port |
| `auth_cookie` | `str` | Yes | JWT cookie name |
| `auth_secret` | `str` | Yes | JWT signing secret |
| `auth_users` | `list[UserConfig]` | Yes | Authorized users |
| `log` | `str` | No | Oracle log destination |
| `auth_exptime` | `int` | No | JWT expiration time (seconds) |
| `debug` | `bool` | No | Enable Flask debug mode (default: `false`) |
| `https_cert` | `str` | No | Path to HTTPS certificate |
| `https_key` | `str` | No | Path to HTTPS private key |

### OracleSessionConfig

Configuration for connecting to another service's Oracle API:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `addr` | `str` | Yes | Target service address |
| `port` | `int` | Yes | Target service port |
| `auth_username` | `str` | Yes | Login username |
| `auth_password` | `str` | Yes | Login password |

## Common Data Structures

### Dialogue Types

Used by the Speaker service and the `DialogueInterface` for LLM-powered conversations:

**`DialogueMood`** — Represents a personality mood for DImROD:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Mood name (e.g., `"arrogant"`, `"complacent"`) |
| `description` | `str` | Behavioral description for the LLM system prompt |
| `chance` | `float` | Probability weight for random mood selection |

Default moods: `arrogant` (0.35), `complacent` (0.25), `impatient` (0.25), `informal_complacent` (0.25), `twang` (0.01), `chill` (0.01).

**`DialogueAuthor`** — Identifies the author of a message:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique author identifier |
| `type` | `DialogueAuthorType` | Author type enum |
| `name` | `str` | Display name |

`DialogueAuthorType` values: `UNKNOWN` (-1), `SYSTEM` (0), `SYSTEM_QUERY_TO_USER` (1), `USER` (1000), `USER_ANSWER_TO_QUERY` (1001).

**`DialogueMessage`** — A single message in a conversation:

| Field | Type | Description |
|-------|------|-------------|
| `author` | `DialogueAuthor` | Who sent the message |
| `content` | `str` | Message text |
| `timestamp` | `datetime` | When the message was sent |
| `id` | `str` | Unique message identifier |
| `telegram_chat_id` | `int` | Associated Telegram chat ID (if any) |
| `telegram_message_id` | `int` | Associated Telegram message ID (if any) |

**`DialogueConversation`** — A sequence of messages:

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[DialogueMessage]` | Ordered list of messages |
| `id` | `str` | Unique conversation identifier |
| `time_start` | `datetime` | Conversation start time |
| `time_latest` | `datetime` | Most recent message time |
| `telegram_chat_id` | `int` | Associated Telegram chat ID |

### NLA Types

Used by the Natural Language Actions framework:

**`NLAService`** — Represents a service that exposes NLA endpoints:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Service name |
| `oracle` | `OracleSessionConfig` | How to connect to the service |

**`NLAEndpoint`** — A single NLA action:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Endpoint name |
| `description` | `str` | Human-readable description of what it does |

**`NLAEndpointInvokeParameters`** — Parameters sent when invoking an NLA endpoint:

| Field | Type | Description |
|-------|------|-------------|
| `message` | `str` | The original user message |
| `substring` | `str` | Relevant portion of the message (optional) |
| `extra_params` | `dict` | Additional parameters (optional) |

**`NLAResult`** — Response from an NLA invocation:

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the action succeeded |
| `message` | `str` | Result message |
| `message_context` | `str` | Additional context |
| `payload` | `dict` | Arbitrary result data |

### Location and Weather Types

**`Location`** (from `lib/lu.py`) — a plain Python class (not a `Uniserdes` subclass):

| Field | Type | Description |
|-------|------|-------------|
| `address` | `str` | Street address |
| `latitude` | `float` | Latitude coordinate |
| `longitude` | `float` | Longitude coordinate |

> **Note:** The Nimbus service defines its own separate `Location` class in `services/nimbus/location.py`, which extends `Uniserdes` and includes an additional `name` field used for location lookup by name. These two `Location` classes are unrelated — see the [Nimbus documentation](services/nimbus.md) for details on the service-specific version.

**`Forecast`** (from Nimbus):

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Forecast period name |
| `description_short` | `str` | Brief forecast |
| `description_long` | `str` | Detailed forecast |
| `temperature_value` | `int` | Temperature reading |
| `temperature_unit` | `str` | Unit (e.g., `"F"`) |
| `wind_speed` | `str` | Wind speed |
| `wind_direction` | `str` | Wind direction |
| `time_start` | `str` (converted to `datetime`) | Forecast period start |
| `time_end` | `str` (converted to `datetime`) | Forecast period end |

## How Services Define Their Own Models

Each service defines its domain models as `Uniserdes` subclasses in separate files alongside the main service file. For example:

* **Chef** defines `Recipe` and `Ingredient` in `recipe.py`
* **Warden** defines `Device`, `KnownDeviceConfig`, `DeviceHardwareAddress`, and `DeviceNetworkAddress` in `device.py`
* **Historian** defines `HistorianEvent` in `event.py`
* **Gatekeeper** defines `GatekeeperEventConfig`, `GatekeeperEventPostConfig`, and `GatekeeperSubscriberConfig` in `event.py` and `subscriber.py`

Service configs extend `ServiceConfig` (or directly `Config`) by adding service-specific fields. For example, `LumenConfig` adds fields for light definitions, webhook keys, and Wyze/LIFX integration settings.

For details on each service's specific models, see the [service documentation](services/).
