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

### Universal String Preprocessing

All string field values are automatically run through a **string preprocessor** during parsing. No opt-in is required — every string value in every Uniserdes model is preprocessed. The pipeline runs in two stages:

#### Stage 1: Environment Variable Expansion

`$VAR` and `${VAR}` syntax is expanded using `os.path.expandvars()`. If a variable is not defined, it is left as-is (no error is raised).

```yaml
api_key: $MY_API_KEY
greeting: "Hello ${USER}!"
```

#### Stage 2: Bang Commands

After environment variable expansion, the string is checked for **bang commands** — directives that begin with `!` followed by a keyword. Leading whitespace is stripped before the `!` check, and whitespace between the keyword and the content is also stripped.

New bang commands can be registered at runtime via the `register_bang()` API (see `lib/uniserdes.py`).

**`!file` — read file contents:**

If the string starts with `!file`, the rest is treated as a file path. The file is read and its contents become the field value.

* Relative paths are resolved against the config file's directory (`base_path`).
* Absolute paths are used as-is.
* If the file does not exist, a `FileNotFoundError` is raised.
* Works with `parse_file()` (which computes `base_path` automatically) and with `parse_json()` when a `base_path` is provided.

```yaml
# Read from a relative path (resolved against the config file's directory)
description: "!file ./descriptions/oil_change.txt"

# Read from an absolute path
description: "!file /etc/dimrod/descriptions/oil_change.txt"

# Compose with environment variables (expanded first, then file is read)
description: "!file $HOME/configs/desc.txt"

# Extra whitespace between keyword and path is stripped
description: "!file   ./descriptions/oil_change.txt"
```

**`!list` — generate a list from an expression:**

If the string starts with `!list`, the rest is evaluated as `list(expression)` in a **restricted namespace** and the resulting Python `list` object becomes the field value directly.

Only safe, side-effect-free builtins are available: `range`, `int`, `float`, `str`, `len`, `list`, `tuple`, `set`, `abs`, `min`, `max`, `sum`, `round`, `sorted`, `reversed`, `enumerate`, `zip`, `map`, `filter`. No access to `os`, `sys`, `import`, `open`, `exec`, or `eval`.

```yaml
# Generate mileage thresholds
mileages: "!list range(5000, 50001, 5000)"
# → [5000, 10000, 15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000]

# Simple range
values: "!list range(5)"
# → [0, 1, 2, 3, 4]

# Extra whitespace is stripped
values: "!list   range(1000, 5001, 1000)"
# → [1000, 2000, 3000, 4000, 5000]
```

Because `!list` returns an actual `list` object, the field receives a native Python list — not a string representation. This means fields typed as `[list]` can use `!list` directly in config files, and the result is immediately usable as a list without additional parsing.

If the expression is invalid, a `ValueError` is raised with a descriptive message.

**No bang command:**

If no recognised bang command is present, the string (after environment variable expansion) is used as-is.

#### Composability

Since environment variables are expanded *before* bang processing, all bang commands compose naturally with environment variables:

```yaml
# $CONFIG_DIR is expanded first, then the file is read
description: "!file $CONFIG_DIR/descriptions/oil_change.txt"
```

#### Extensibility

The bang system is designed to be extensible. New commands can be added by calling `register_bang()`:

```python
from lib.uniserdes import register_bang

def _my_handler(content: str, base_path: str) -> str:
    return content.upper()

register_bang("upper", _my_handler)
# Now "!upper hello world" → "HELLO WORLD"
```

> **Note:** Bang handlers typically return `str`, but may return other types. For example, the built-in `!list` handler returns a Python `list` object directly.

#### Notes

* Only raw `str` values are preprocessed. Non-string types (`int`, `float`, `list`, `dict`, etc.) are never preprocessed.
* Nested Uniserdes objects are preprocessed when their own `parse_json()` runs — the parent does not preprocess their string values.
* Default values for absent fields are **not** preprocessed.

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

## Enums

### `Weekday`

Represents days of the week using a 0-indexed Sunday-start convention. Defined in `lib/dtu.py`.

| Value | Name |
|-------|------|
| `0` | `SUNDAY` |
| `1` | `MONDAY` |
| `2` | `TUESDAY` |
| `3` | `WEDNESDAY` |
| `4` | `THURSDAY` |
| `5` | `FRIDAY` |
| `6` | `SATURDAY` |

### `Month`

Represents calendar months using 1-indexed values. Defined in `lib/dtu.py`.

| Value | Name |
|-------|------|
| `1` | `JANUARY` |
| `2` | `FEBRUARY` |
| `3` | `MARCH` |
| `4` | `APRIL` |
| `5` | `MAY` |
| `6` | `JUNE` |
| `7` | `JULY` |
| `8` | `AUGUST` |
| `9` | `SEPTEMBER` |
| `10` | `OCTOBER` |
| `11` | `NOVEMBER` |
| `12` | `DECEMBER` |

## DatetimeTrigger

`DatetimeTrigger` is a general-purpose `Uniserdes` subclass defined in `lib/dtu.py` for matching datetimes against a set of field constraints. It can be used by any service that needs schedule-based triggering (e.g., Gearhead for time-based maintenance, or any future scheduling use case).

### `MaintenanceLogEntryStatus`

Represents the status of a maintenance log entry. Defined in `services/gearhead/maintenance_log.py`.

| Value | Name | Description |
|-------|------|-------------|
| `0` | `PENDING` | A Todoist task has been created; user has not yet completed it |
| `1` | `DONE` | The maintenance has been completed |

In JSON, status is serialized as its integer value (0 or 1). When parsing, both integer values and string names (`"pending"`, `"done"`, `"PENDING"`, `"DONE"`) are accepted.

### Fields

| Field | Type | Required | Default | Valid Range | Description |
|-------|------|----------|---------|-------------|-------------|
| `years` | `list[int]` | No | `[]` | Any valid year | Specific years to match |
| `months` | `list[Month]` | No | `[]` | `JANUARY`–`DECEMBER` (1–12) | Calendar months to match |
| `days` | `list[int]` | No | `[]` | 1–31 or -31 to -1 | Day of month; negative values count from end (-1 = last day) |
| `weekdays` | `list[Weekday]` | No | `[]` | `SUNDAY`–`SATURDAY` (0–6) | Days of week to match |
| `hours` | `list[int]` | No | `[]` | 0–23 | Hour of day (24-hour clock) |
| `minutes` | `list[int]` | No | `[]` | 0–59 | Minute of hour |

### Matching Semantics

- **Empty field = wildcard**: If a field is `[]`, it matches any value for that datetime component.
- **AND between fields**: A datetime must satisfy ALL non-empty field constraints simultaneously.
- **OR within a field**: Within a single field, ANY value matching is sufficient.

**Example:** `months=[3, 6, 9, 12], weekdays=[1]` means "any Monday in March, June, September, or December."

### Negative Day Values

Negative values in the `days` field count backwards from the end of the month:

- `-1` = last day of the month (e.g., Jan 31, Feb 28/29, Apr 30)
- `-2` = second-to-last day
- `-n` = nth-to-last day

### Methods

**`matches(dt)`** — Returns `True` if the given `datetime` satisfies all trigger conditions.

**`matches_range(dt_start, dt_end)`** — Returns `True` if ANY datetime within the half-open interval `[dt_start, dt_end)` satisfies the trigger conditions. Uses day-granularity iteration with early termination.

**`check_fields()`** — Validates all trigger field values are within legal ranges. Called automatically during parsing.

### JSON Representation

`Month` and `Weekday` enum values are serialized as integers in JSON:

```json
{
    "years": [],
    "months": [3, 6, 9, 12],
    "days": [-1],
    "weekdays": [1],
    "hours": [9],
    "minutes": [0]
}
```

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
