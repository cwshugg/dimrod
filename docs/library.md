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
├── govee.py            # Govee lights & plugs (Developer API v2)
├── ifttt.py            # IFTTT webhook sender
├── dtu.py              # Date/time utilities
├── lu.py               # Location utilities
├── db.py               # SQLite wrapper
├── mail.py             # Email sender (via IFTTT)
├── email_client.py     # IMAP/SMTP mailbox transport (listen + reply)
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
| `get_light_by_name(name)` | Find a bulb by label (forces one re-discovery on a cache miss) |
| `set_light_power(light, action)` | Turn a light on or off (acknowledged + verified) |
| `set_light_color(light, color)` | Set a light's color |
| `set_light_brightness(light, brightness)` | Set brightness level |
| `refresh()` | Re-scan the network for bulbs |

**Reliability behavior.** Power commands are **acknowledged and verified** rather
than fire-and-forget:

- `set_light_power` sends the LAN `SetPower` with an acknowledgement requested
  (`rapid=False`), so a lost/timed-out command raises and the existing
  `retry_attempts`/`retry_delay` retry loop actually engages (previously, a
  fire-and-forget `rapid=True` send never raised, leaving the retry loop inert).
- After each send, the bulb's power state is **read back** with `get_power()` and
  compared to the requested state. A mismatch — or a failed read-back — is treated
  as a retryable failure. If all `retry_attempts` are exhausted, the last error is
  raised (a `LIFXError` for an unverifiable state), so callers can log a real
  failure instead of a false success.
- `get_light_by_name` is **self-healing**: on a cache miss it forces exactly one
  fresh discovery and re-checks before giving up (returning `None` only if the
  bulb is still absent), so a bulb missed during an earlier flaky discovery pass
  is not un-commandable for the full 2-hour cache window. There is no re-discovery
  loop.
- Successive LAN commands are gently **staggered** by `command_delay` seconds
  (see config below) so a burst of per-bulb toggles from multiple worker threads
  (e.g. 5 kitchen bulbs) is not sent in the same instant. Mirrors the Govee
  `command_delay` precedent. This is now a minor spacing knob — the real
  concurrency guarantee comes from serialization (below).

**Thread safety (serialized LAN access).** A single instance of `LIFX` is shared
by all of Lumen's action worker threads, and `lifxlan`'s `Device` manages its
UDP sockets through a process-global, **un-locked** socket table. Concurrent
socket open/close across threads — or one thread rebuilding the shared `LifxLAN`
via `refresh()` while another was mid-command — tore sockets down under in-flight
commands and produced intermittent `[Errno 9] Bad file descriptor` failures
(only some bulbs in a group turning on). To fix this:

- Every public operation that touches `lifxlan` (`get_lights`, `get_light_by_name`,
  `get_light_by_address`, `set_light_power` incl. its verify read-back,
  `set_light_color`, `set_light_brightness`, `refresh`, `handle_error`) holds a
  single **reentrant lock** (`threading.RLock`) for its full body, so LAN access
  is serialized and no two threads ever race on the shared sockets. The lock is
  reentrant so nested internal calls do not deadlock.
- The shared `LifxLAN` is **no longer rebuilt on a transient command retry**: a
  lost ack or a failed read-back is retried on the *same* object. `refresh()`
  (which tears down sockets) runs only under the lock and only for genuine
  discovery problems. This supersedes the pure-stagger approach as the actual
  fix. Because LAN calls are fast, serializing a handful of bulbs is
  imperceptible.

**`LIFXConfig` fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `refresh_delay` | int | `7200` | Seconds before the discovery cache is considered stale |
| `retry_attempts` | int | `4` | Attempts for discovery and (now-effective) power commands |
| `retry_delay` | int/float | `0.1` | Seconds slept between retry attempts |
| `command_delay` | int/float | `0.05` | Seconds to space successive LAN commands across all bulbs (0 disables the stagger) |

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

### `govee.py` — Govee Lights & Plugs

A `requests`-based client for the **Govee Developer API v2**, used by Lumen to
control Govee lights and plugs with a single cloud call (replacing a slow IFTTT
hop). See the [Govee integration guide](services/govee-integration.md) for
setup and configuration.

**`Govee` methods:**

| Method | Description |
|--------|-------------|
| `get_devices(refresh)` | Fetch account devices (cached for `refresh_delay`) |
| `get_device_by_name(name)` | Find a device by its Govee-app name |
| `get_device_by_address(mac, sku)` | Find (or build) a device by MAC + model |
| `get_device_state(device)` | Query a device's state and online status |
| `set_device_power(device, action)` | Turn a device on or off |
| `set_device_brightness(device, brightness)` | Set brightness (`0.0`–`1.0` → `1`–`100`) |
| `set_device_color(device, color)` | Set color (`[r, g, b]` → packed int) |
| `toggle_plug(device, power_on)` | Turn an `H5083` plug on or off |

`GoveeConfig` holds the (secret) `api_key`, request-tuning fields, and an
optional `{id, sku, mac}` device map. Value conversions and rate-limit backoff
are handled internally.

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

### `email_client.py` — IMAP/SMTP Mailbox Transport

A reusable wrapper that both **listens to** and **replies from** a real email
mailbox over IMAP + SMTP. This is distinct from `mail.py` (a one-way IFTTT email
sender): `email_client.py` owns a live mailbox connection for reading, IDLE
waiting, threaded replies, and permanent deletion. It is the transport used by
the `mailman` service and is intentionally provider-portable (Gmail-friendly
defaults, all overridable).

Implemented with the **Python standard library only** (`imaplib`, `smtplib`,
`email`) — no third-party dependency is added to `services/lib/requirements.txt`,
so nothing extra is installed into any service venv.

* `EmailClientConfig` — Config for the IMAP + SMTP connection: `imap_host`,
  `imap_port` (993), `imap_ssl` (true), `smtp_host`, `smtp_port` (587),
  `smtp_ssl` (false), `username`, `password` (an **app-password secret — never
  logged**), optional `from_address`/`from_name`, `mailbox` (INBOX),
  `imap_timeout`/`smtp_timeout` (30s), `idle_refresh_interval` (1740s ≈ 29 min),
  `delete_mode` (`gmail_trash_expunge` default, or `expunge`), and
  `gmail_trash_folder` (`[Gmail]/Trash`).
* `EmailClient(config, log=None)` — The transport wrapper.
    * `connect()` / `disconnect()` — Open/close and authenticate the IMAP + SMTP
      connections.
    * `idle_wait(timeout)` — Issue IMAP IDLE and block until a new-mail (EXISTS)
      event or timeout; proactively reconnects the IDLE connection every
      ~29 min and transparently reconnects on drop. Returns `True` on a new-mail
      event, `False` on timeout/reconnect.
    * `search_unseen()` — Return the UIDs of `UNSEEN` messages.
    * `refresh()` — Issue an IMAP `NOOP` to flush the server's pending untagged
      `EXISTS` updates so messages that arrived **after** the last `select()`
      become visible/fetchable on a long-lived connection. Cheaper than a full
      re-`SELECT` and sufficient per RFC 3501; raises on a non-OK response.
    * `fetch(uid)` — Fetch a message with `BODY.PEEK[]` (does **not** set
      `\Seen`) and return a `ParsedEmail` exposing `from_address`, `subject`,
      `message_id`, `in_reply_to`, `references`, plain-text `body_text`, and the
      raw `.message` (`email.message.EmailMessage`).
    * `mark_seen(uid)` — Explicitly flag a message `\Seen`.
    * `build_reply(original, body_text)` — Build a threaded reply
      (`Subject: Re: …` with empty-subject ⇒ `Re:` and no double-prefix,
      `To` = original sender, correct `In-Reply-To`/`References`) as an
      `EmailMessage`.
    * `send(message)` — Send an `EmailMessage` over authenticated SMTP.
    * `delete(uid, message_id=None)` — **Permanently** remove a message. On Gmail
      (`gmail_trash_expunge`) it copies to `[Gmail]/Trash`, removes it from the
      source mailbox, then expunges only the matching message out of Trash (a
      plain `\Deleted`+`EXPUNGE` only archives on Gmail); `expunge` mode does the
      RFC-standard in-place delete for other providers. The Trash message is
      located **unambiguously** by its RFC 5322 `Message-ID` (pass `message_id`).
      **Fail-safe:** if the copied message cannot be unambiguously identified in
      Trash (no `message_id`, or no header match), the Trash expunge is **skipped**
      rather than guessing — no arbitrary/"newest" message is ever expunged, so an
      unidentified delete can never destroy the wrong email. The message is still
      removed from the source mailbox and left safely in Trash for manual/auto
      (Gmail ~30-day) cleanup, and a warning is logged. A failed post-delete
      re-`SELECT` of the source mailbox is surfaced as `EmailClientError` so the
      caller forces a reconnect instead of continuing on a bad connection.
* `EmailClientError` — Raised on connection/auth/send/IMAP failures.

**Important:** this file is named `email_client.py` (not `email.py`) so it can
never shadow the standard-library `email` package it depends on.

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
