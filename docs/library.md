# Shared Library

The shared library at `services/lib/` provides the framework and utilities that all DImROD services are built on. It includes the core service architecture, data serialization, external API wrappers, smart home integrations, and common utilities.

## Overview

```
services/lib/
├── uniserdes.py        # Universal data serialization framework
├── config.py           # Configuration base classes
├── service.py          # Service base class (worker thread)
├── oracle.py           # HTTP API server + inter-service client
├── cli.py              # Service launcher CLI
├── nla.py              # Natural Language Actions framework
├── log.py              # Logging
├── dialogue.py         # OpenAI LLM integration
├── todoist.py          # Todoist API wrapper
├── ynab.py             # YNAB budgeting API wrapper
├── news.py             # NewsAPI wrapper
├── lifx.py             # LIFX smart lighting (LAN)
├── wyze.py             # Wyze smart home devices
├── ifttt.py            # IFTTT webhook sender
├── dtu.py              # Date/time utilities
├── lu.py               # Location utilities
├── db.py               # SQLite wrapper
├── mail.py             # Email sender (via IFTTT)
├── ntfy.py             # Push notifications (ntfy.sh)
├── requirements.txt    # Shared Python dependencies
├── google/
│   ├── google_auth.py      # Google service account auth
│   └── google_calendar.py  # Google Calendar API wrapper
└── garmin/
    ├── garmin.py        # Garmin Connect API wrapper
    └── database.py      # Garmin data SQLite storage
```

## Core Framework

### `uniserdes.py` — Data Serialization

The **Uniserdes** (Universal Serializer/Deserializer) class is the foundation of all data modeling in DImROD. Every config, API payload, and database record inherits from it.

**Key classes:**

* `UniserdesField(name, types, required, default)` — Typed field descriptor
* `Uniserdes` — Base data class with serialization methods

**Serialization methods:**

| Method | Description |
|--------|-------------|
| `to_json()` / `from_json()` | JSON dict conversion |
| `parse_file()` / `from_file()` | Load from file (YAML or JSON) |
| `to_bytes()` / `parse_bytes()` | UTF-8 byte conversion |
| `to_hex()` / `parse_hex()` | Hex string conversion |
| `to_csv()` | CSV string (one row) |
| `to_sqlite3()` / `from_sqlite3()` | SQLite3 tuple conversion |
| `get_sqlite3_table_definition()` | Generate CREATE TABLE DDL |
| `copy()` | Deep copy via JSON round-trip |

Handles nested objects, enums, datetimes, and unknown fields (stored in `extra_fields`) automatically. See [Data Types](data-types.md) for full details.

### `config.py` — Configuration Base Classes

Thin wrappers over `Uniserdes` for semantic clarity:

* `ConfigField` — Alias for `UniserdesField`
* `Config` — Alias for `Uniserdes`

All service configs use these as their base classes.

### `service.py` — Service Base Class

Defines the `Service` class (extends `threading.Thread`), which is the background worker for every DImROD service.

**`ServiceConfig` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `service_name` | `str` | Service identifier |
| `service_log` | `str` | Log output destination |
| `msghub_name` | `str` | ntfy.sh notification topic |
| `oracle` | `OracleConfig` | HTTP API config |

**`Service` lifecycle:**

1. `__init__(config_path)` — Loads config, creates a `Log` instance, creates an `NtfyChannel` as `self.msghub`, initializes a threading `Lock`
2. `run()` — Override this to implement the service's main loop (default just logs a message)

Services extend this class and typically implement a tick-based loop that performs periodic work.

### `oracle.py` — HTTP API Server

The `Oracle` class (extends `threading.Thread`) is a Flask-based HTTP server that exposes each service's API.

**Built-in endpoints (every Oracle):**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Root/health check |
| `GET` | `/id` | Service identity |
| `POST` | `/auth/login` | Authenticate and receive a JWT cookie |
| `GET` | `/auth/check` | Verify current authentication |
| `POST` | `/msghub/post` | Post a push notification to the service's ntfy channel |
| `GET` | `/nla/get` | List registered NLA endpoints |
| `POST` | `/nla/invoke/<endpoint>` | Invoke an NLA endpoint |

**JWT authentication flow:**

1. Client sends `POST /auth/login` with `{"username": "...", "password": "..."}`
2. Oracle validates credentials against its `auth_users` list
3. On success, returns a signed JWT as a cookie (HS512 algorithm)
4. Subsequent requests include this cookie; Oracle validates it on each request
5. Users with `privilege == 0` have no token expiration

**Production vs. debug:**

* By default, Oracle runs a `gevent.pywsgi.WSGIServer` for production use
* Setting `debug: true` in the Oracle config switches to Flask's built-in development server

**`OracleSession`** — HTTP client for inter-service communication:

| Method | Description |
|--------|-------------|
| `login()` | Authenticate with the target service |
| `post(endpoint, payload)` | Send a POST request with JSON body |
| `get(endpoint)` | Send a GET request |
| `get_response_status(r)` | Extract HTTP status code |
| `get_response_json(r)` | Extract JSON body |
| `get_response_success(r)` | Check if the request succeeded |
| `get_response_message(r)` | Extract the response message |

### `cli.py` — Service Launcher

The `ServiceCLI` class provides a standardized entry point for all services.

**Command-line arguments:**

| Argument | Description |
|----------|-------------|
| `--config CONFIG.yaml` | Path to the service's config file (required; YAML or JSON) |
| `--oracle` | Enable the HTTP API server (flag) |

**Startup sequence:**

1. Parse command-line arguments
2. Load the config file into the service's config class
3. Verify the process is running inside a Python virtual environment
4. Create the `Service` instance
5. Optionally create the `Oracle` instance
6. Install `SIGINT` handler for graceful shutdown
7. Start both threads and block until they exit

### `nla.py` — Natural Language Actions

The NLA framework allows services to advertise capabilities that can be invoked via natural language.

**Key classes:**

* `NLAService` — Represents a service with NLA capabilities (name + connection details)
* `NLAEndpoint` — A single invokable action (name + description + handler function)
* `NLAEndpointInvokeParameters` — Parameters passed when invoking an endpoint (message, substring, extra params)
* `NLAResult` — Result of an invocation (success, message, context, payload)

Services register NLA endpoints by overriding `Oracle.init_nla()`, which makes them automatically available at `/nla/invoke/<name>`.

### `log.py` — Logging

Simple timestamped logger:

* `Log(name, stream)` — Create a logger with a name prefix, writing to stdout/stderr or a file
* `write(msg)` — Write a prefixed, timestamped log entry
* `rent_fd()` / `return_fd()` — Borrow/return the underlying file descriptor

## External API Wrappers

### `dialogue.py` — OpenAI LLM Integration

Wraps OpenAI's chat completion API to give DImROD a conversational personality.

**Key classes:**

* `DialogueConfig` — API key, model name (default: `gpt-4o-mini`), behavior prompt, mood list, database settings
* `DialogueMood` — A personality mode with a name, description, and activation probability
* `DialogueAuthor` — Message author with ID, type (`SYSTEM`, `USER`, etc.), and name
* `DialogueMessage` — A single message with author, content, timestamp, and optional Telegram metadata
* `DialogueConversation` — An ordered sequence of messages with metadata
* `DialogueInterface` — The main interface for LLM interactions

**`DialogueInterface` methods:**

| Method | Description |
|--------|-------------|
| `talk(prompt, conversation, author, intro)` | Continue a conversation with the LLM |
| `oneshot(intro, prompt)` | Single LLM query with no conversation context |
| `reword(prompt, extra_context)` | Reword text using the LLM |
| `remood(new_mood)` | Change DImROD's current mood (random weighted selection) |
| `prune()` | Remove old conversations from the database |
| `save_conversation(conv)` | Persist a conversation to SQLite |
| `search_conversation(cid)` | Retrieve a conversation by ID |
| `save_message(msg, conv)` | Save a message to a conversation's table |
| `search_message(...)` | Search messages by various criteria |

**Mood system:** DImROD has randomized personality moods (arrogant, complacent, impatient, etc.) that influence LLM responses. Moods are selected by weighted random probability and can change over time.

**`dialogue_chat_completion()`** — A standalone function that wraps the OpenAI async API using `asyncio.run()` and returns the assistant's response text. Used internally by `DialogueInterface` methods.

### `todoist.py` — Todoist Task Management

Wraps the Todoist REST API with local caching.

**`Todoist` key methods:**

| Method | Description |
|--------|-------------|
| `get_projects()` / `get_project_by_name(name)` | List/find projects |
| `get_sections()` / `get_section_by_name(name)` | List/find sections |
| `get_tasks()` / `get_task_by_title(title)` | List/find tasks |
| `add_task(...)` / `update_task(...)` / `delete_task(...)` | Task CRUD |
| `move_task(task, section)` | Move a task to a different section |
| `add_project(name)` / `add_section(name, project)` | Create projects/sections |

Caches projects, sections, and tasks locally with a 15-second refresh interval.

### `ynab.py` — YNAB Budgeting

Wraps the YNAB (You Need A Budget) API for budget and transaction management.

**Key classes:**

* `YNABTransactionInfo` — Read-only view of a transaction (account, payee, category, amount, date, etc.)
* `YNABTransactionUpdate` — Mutable transaction update descriptor with fields for account, payee, amount, category, description, cleared status, flag color

**`YNAB` key methods:**

| Method | Description |
|--------|-------------|
| `get_budgets()` / `get_budget_by_id(id)` | Budget lookup |
| `get_accounts(budget)` / `get_account_by_id(budget, id)` | Account lookup |
| `get_categories(budget)` / `get_category_by_id(budget, id)` | Category lookup |
| `get_transactions(budget, ...)` | List transactions (with filters) |
| `get_transactions_unapproved(budget)` | Unapproved transactions |
| `get_transactions_uncategorized(budget)` | Uncategorized transactions |
| `update_transactions(budget, updates)` | Batch update transactions |

### `news.py` — NewsAPI

Wraps the NewsAPI for fetching news headlines and articles.

**Key classes:**

* `NewsAPIQueryArticles` — Article query with terms, sources, date range, sort order, and max count
* `NewsAPIQuerySources` — Source query with country, language, and category filters

**`NewsAPI` methods:** `query_sources(query)`, `query_articles(query)` (supports automatic pagination).

## Smart Home / IoT

### `lifx.py` — LIFX Smart Lighting

Controls LIFX bulbs over the local network using the LAN protocol.

**`LIFX` methods:**

| Method | Description |
|--------|-------------|
| `get_lights(refresh)` | Discover all LIFX bulbs on the network |
| `get_light_by_name(name)` | Find a bulb by label |
| `set_light_power(light, action)` | Turn a light on or off |
| `set_light_color(light, color)` | Set a light's color |
| `set_light_brightness(light, brightness)` | Set brightness level |
| `refresh()` | Re-scan the network for bulbs |

Configurable retry attempts and delays for network reliability.

### `wyze.py` — Wyze Smart Home

Controls Wyze devices (primarily smart plugs) via the Wyze SDK.

**`Wyze` methods:**

| Method | Description |
|--------|-------------|
| `login()` | Authenticate with Wyze (with retry logic) |
| `refresh()` | Re-authenticate |
| `get_devices()` | List all Wyze devices |
| `get_plug(macaddr)` | Find a plug by MAC address |
| `toggle_plug(macaddr, power_on)` | Turn a plug on or off |

### `ifttt.py` — IFTTT Webhooks

Sends webhooks to IFTTT to trigger applets.

**`Webhook` methods:**

| Method | Description |
|--------|-------------|
| `send(event, jdata)` | Fire a webhook event with JSON data |
| `get_status_code(response)` | Extract status code from response |
| `get_errors(response)` | Extract error messages |

## Utilities

### `dtu.py` — Date/Time Utilities

Comprehensive date and time helper functions and types.

**Enums:** `Weekday` enum (`SUNDAY=0` through `SATURDAY=6`), `Month` enum (`JANUARY=1` through `DECEMBER=12`).

**`DatetimeTrigger`** — A general-purpose `Uniserdes` subclass for matching datetimes against schedule constraints. Supports six optional list fields (`years`, `months`, `days`, `weekdays`, `hours`, `minutes`), with empty lists acting as wildcards. Provides `matches(dt)` for single-datetime matching and `matches_range(dt_start, dt_end)` for range matching using day-granularity iteration. See [Data Types — DatetimeTrigger](data-types.md#datetimetrigger) for full field specifications and semantics.

**Weekday operations:** `get_weekday()`, `is_weekend()`, `is_weekday()`, day-distance calculations.

**Time-of-day checks:** `is_morning()`, `is_afternoon()`, `is_evening()`, `is_night()`, `is_workhours()`.

**Season detection:** `is_spring()`, `is_summer()`, `is_fall()`, `is_winter()`.

**Date arithmetic:** `add_seconds()`, `add_minutes()`, `add_hours()`, `add_days()`, `add_weeks()`, along with corresponding `diff_in_*()` functions.

**Parsing:** `parse_datetime(args)` accepts flexible natural language inputs including dates, weekday names, clock times (`3:30pm`), and relative offsets (`1h`, `2d`, `next Tuesday`).

**Formatting:** `format_yyyymmdd()`, `format_yyyymmdd_hhmmss_24h()`, `format_yyyymmdd_hhmmss_12h()`.

### `lu.py` — Location Utilities

Geocoding, timezone, and sunrise/sunset calculations.

* `Location(address, latitude, longitude)` — Represents a geographic location
* `LOCATION_DEFAULT` — Default location, defined by coordinates (`latitude=35.786..., longitude=-78.681...`, corresponding to Raleigh, NC)
* `get_timezone(loc)` — Get timezone for a location using `timezonefinder`
* `get_sunrise_sunset(loc, dt)` — Get sunrise/sunset times from the sunrise-sunset.org API
* `get_sunrise(loc, dt)` / `get_sunset(loc, dt)` — Individual sunrise/sunset lookups

Uses `geopy` for geocoding (Nominatim) and `pytz` for timezone handling.

### `db.py` — SQLite Wrapper

Convenience wrapper around Python's `sqlite3` module.

**`Database` methods:**

| Method | Description |
|--------|-------------|
| `get_connection(reset)` | Get (or create) a cached connection |
| `close_connection()` | Close the current connection |
| `execute(query, do_commit)` | Execute a raw SQL query |
| `table_exists(table)` | Check if a table exists |
| `get_all_table_names()` | List all tables |
| `get_table_column_names(table)` | List columns in a table |
| `search(table, condition, order_by, desc, limit)` | Query rows with a WHERE clause, optional ordering and limit |
| `search_order_by(table, condition, ...)` | Query with ordering |
| `insert_or_replace(table, values, do_commit)` | Insert a row, or replace if the primary key already exists |
| `table_to_csv(table, condition)` | Export a table to CSV string |
| `export_to_excel(path, table_names)` | Export tables to Excel file |

The `search()` method supports optional parameters for ordering and limiting results:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table` | `str` | — | Table name to query |
| `condition` | `str` | — | SQL WHERE clause |
| `order_by` | `str` | `None` | Column name to order results by |
| `desc` | `bool` | `False` | If `True`, order descending |
| `limit` | `int` | `None` | Maximum number of rows to return |

The `insert_or_replace()` method performs an `INSERT OR REPLACE` operation, inserting a new row or replacing an existing row if a row with the same primary key already exists. The `values` parameter should be a SQL-formatted values string (e.g., the output of `Uniserdes.to_sqlite3_str()`). Commits by default unless `do_commit` is set to `False`.

### `mail.py` — Email Sender

Sends emails via IFTTT webhooks.

* `Messenger(config)` — Wraps an IFTTT `Webhook` to send emails
* `send(email, subject, content)` — Send an email with `to`, `subject`, and `content` fields

### `ntfy.py` — Push Notifications

Publishes push notifications to [ntfy.sh](https://ntfy.sh).

* `ntfy_send(topic, message, title, tags, priority)` — Send a one-off notification
* `NtfyChannel(name)` — A reusable channel bound to a topic
    * `post(message, title, tags, priority)` — Publish a notification

Notifications are sent as JSON with Markdown support enabled.

## Sub-Modules

### `google/` — Google API Integration

**`google_auth.py`** — Google service account authentication:

* `GoogleCredentials(scopes, service_account_path)` — Loads service account credentials
* `authenticate()` — Returns authenticated credentials for Google API calls

**`google_calendar.py`** — Google Calendar API wrapper:

* `GoogleCalendarConfig` — Config with service account path and OAuth scopes
* `GoogleCalendar` — Calendar API client

| Method | Description |
|--------|-------------|
| `get_events(calendar_id, ...)` | Fetch events with optional time bounds |
| `get_events_after(calendar_id, dt)` | Events after a given datetime |
| `get_events_between(calendar_id, start, end)` | Events in a date range |
| `create_event(calendar_id, title, start, end, ...)` | Create a new event |

Helper methods: `make_calendar_time()`, `get_event_start()`, `get_event_end()`, `get_event_title()`, `get_event_description()`.

### `garmin/` — Garmin Connect Integration

**`garmin.py`** — Garmin Connect API wrapper:

* `GarminLoginStatus` enum: `SUCCESS`, `FAILURE`, `BAD_CREDENTIALS`, `NEED_2FA`, `RATE_LIMITED`, `BAD_2FA_CODE`
* `GarminConfig` — Account credentials, 2FA Telegram chat ID, token store directory
* `Garmin` — API client with login flows and data retrieval

**Login methods:** `login_with_credentials()`, `login_with_2fa(code)`, `login_with_tokenstore()` — supports credential-based login, two-factor authentication, and cached token reuse.

**Data retrieval methods:**

| Method | Description |
|--------|-------------|
| `get_steps_for_day_range(start, end)` | Step count data |
| `get_sleep_for_day_range(start, end)` | Sleep analysis data |
| `get_heart_rate_for_day_range(start, end)` | Heart rate data |
| `get_vo2max_for_day_range(start, end)` | VO2 max readings |
| `get_activities_for_day_range(start, end)` | Exercise activities |
| `get_floors_for_day_range(start, end)` | Floors climbed |

**`database.py`** — SQLite storage layer for Garmin data:

* `GarminDatabaseConfig` — Database file path
* `GarminDatabase` — Persistence layer with per-metric save/search methods

**Data entry models (all extend `GarminDatabaseEntryBase`):**

| Entry Class | Fields |
|-------------|--------|
| `GarminDatabaseStepsEntry` | `time_start`, `time_end`, `step_count`, `push_count`, `activity_level` |
| `GarminDatabaseSleepEntry` | `time_start`, `time_end`, sleep durations by stage, respiration stats, heart rate |
| `GarminDatabaseHeartRateEntry` | `timestamp`, `heartrate` |
| `GarminDatabaseHeartRateSummaryEntry` | `timestamp`, min/max/resting heart rate, 7-day resting average |
| `GarminDatabaseVO2MaxEntry` | `timestamp`, `vo2max`, `fitness_age` |
| `GarminDatabaseActivityEntry` | Activity type, duration, distance, calories, heart rate zones, nested exercise sets |
| `GarminDatabaseExerciseSetEntry` | `category`, `reps`, `sets`, `weight_max`, `volume`, `duration` |

Each entry class provides `from_garmin_json()` class methods for parsing API responses.
