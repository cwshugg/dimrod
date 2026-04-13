# Telegram — Chat Bot Interface

Telegram is the primary user-facing interface for DImROD. It runs a Telegram bot that users interact with to chat, issue commands, and receive notifications.

## Purpose

* Provide a conversational interface to DImROD via Telegram messaging
* Expose bot commands for controlling smart home devices, checking weather, managing reminders, and more
* Serve as a notification channel for other services (reminders, alerts, reports)
* Provide an Oracle API so other services can send messages, menus, and questions to users

## Architecture

The Telegram service runs:

* **`TelegramService`** — Manages the Telegram bot polling loop, whitelisted chats/users, command registration, and a background menu-pruning thread
* **`TelegramOracle`** — HTTP API for other services to send messages, menus, and questions through the bot

The bot uses polling (not webhooks) to receive messages and includes automatic restart logic if the polling loop fails.

### Conversation Tracking

Each whitelisted chat maintains its own conversation with DImROD via the Speaker service. When a user sends a message that isn't a command, it's forwarded to Speaker's `/talk` endpoint and the response is sent back to the chat.

### Menu System

Telegram supports interactive button menus backed by a SQLite database. Menus can be sent to users with callback buttons. The menu behavior is controlled by the `MenuBehaviorType` enum:

* **`ACCUMULATE` (0)** — All options can be selected; each accumulates a count of the number of times it was selected
* **`MULTI_CHOICE` (1)** — Multiple options can be selected, but each option's selection count caps at 1
* **`SINGLE_CHOICE` (2)** — Only one option can be selected at a time; selecting one deselects others

Menus have configurable expiration times and are automatically pruned by a background thread.

## Bot Commands

Users interact with DImROD through `/`-prefixed commands:

| Command | Description | Backend Service |
|---------|-------------|-----------------|
| `/lights` | Toggle smart home lights | Lumen |
| `/network` | View connected network devices | Warden |
| `/remind` | Create or view reminders | Notif |
| `/calendar` | View upcoming calendar events | Google Calendar |
| `/budget` | View budget information | YNAB |
| `/news` | Get news headlines | NewsAPI |
| `/recipes` | Search recipes | Chef |
| `/system` | System status and service restart | (local) |
| `/mode` | Switch DImROD's mood | Speaker |
| `/help` | List available commands | (local) |

Commands are defined as individual modules under `commands/`, each implementing a `TelegramCommand` class.

> **Note:** Additional secret/debug commands exist (e.g., `/_reset` for resetting the current chat conversation). These are hidden from the help menu and are intended for development and debugging use.

## Oracle Endpoints

These endpoints allow other DImROD services to interact with users through the Telegram bot.

### Chat/User Lookup

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/bot/chats` | List whitelisted chats |
| `GET` | `/bot/users` | List whitelisted users |

### Message Operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/bot/send/message` | Send a text message to a chat/user |
| `POST` | `/bot/update/message` | Edit an existing message |
| `POST` | `/bot/update/message/reaction` | Add a reaction emoji to a message |
| `POST` | `/bot/delete/message` | Delete a message |
| `POST` | `/bot/delete/message/reaction` | Remove a reaction from a message |

### Interactive Features

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/bot/send/question` | Send a question and wait for the user's response |
| `POST` | `/bot/send/menu` | Send an interactive button menu |
| `POST` | `/bot/update/menu` | Update an existing menu |
| `POST` | `/bot/remove/menu` | Remove a menu from a chat |
| `POST` | `/bot/get/menu` | Retrieve a menu by ID |

### Common Request Fields

Most message endpoints accept:

| Field | Description |
|-------|-------------|
| `text` | Message content |
| `chat` | Whitelisted chat name |
| `user` | Whitelisted user name |
| `chat_id` | Direct Telegram chat ID |
| `message_id` | Telegram message ID (for update/delete operations) |
| `parse_mode` | Telegram parse mode (e.g., `"Markdown"`, `"HTML"`) |

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `bot_api_key` | `str` | Telegram Bot API token |
| `bot_chats` | `list` | Whitelisted chat definitions |
| `bot_users` | `list` | Whitelisted user definitions |
| `lumen` | `OracleSessionConfig` | Connection to Lumen service |
| `warden` | `OracleSessionConfig` | Connection to Warden service |
| `notif` | `OracleSessionConfig` | Connection to Notif service |
| `speaker` | `OracleSessionConfig` | Connection to Speaker service |
| `chef` | `OracleSessionConfig` | Connection to Chef service |
| `moder` | `OracleSessionConfig` | Connection to mood/mode service |
| `google_calendar_config` | `GoogleCalendarConfig` | Google Calendar credentials |
| `google_calendar_id` | `str` | Calendar ID to read events from |
| `google_calendar_timezone` | `str` | Timezone for calendar display |
| `ynab` | `YNABConfig` | YNAB API credentials |
| `news` | `NewsAPIConfig` | NewsAPI credentials |
| `news_default_queries` | `list` | Default news query configurations |
| `bot_error_retry_attempts` | `int` | Number of retry attempts on bot errors (default: `8`) |
| `bot_error_retry_delay` | `int` | Delay between error retries in seconds (default: `1`) |
| `bot_conversation_timeout` | `int` | Conversation inactivity timeout in seconds (default: `900`) |
| `bot_menu_db` | `str` | Path to the SQLite database for menu persistence (default: `None`) |
| `bot_menu_db_refresh_rate` | `int` | Menu database refresh interval in seconds (default: `60`) |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`, `lib.dialogue`, `lib.google.google_calendar`, `lib.ynab`, `lib.news`
* **Python packages:** `pyTelegramBotAPI` (service-specific dependency)
* **Other services:** Speaker, Lumen, Warden, Notif, Chef, Gatekeeper (via OracleSession)
* **External APIs:** Google Calendar, YNAB, NewsAPI (called directly, not via other services)
