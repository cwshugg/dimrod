# Notif — Reminder System

Notif is a cron-like reminder service that manages, schedules, and delivers reminders through multiple channels.

## Purpose

* Load and manage reminder definitions from JSON files
* Check reminder triggers on each tick and deliver due reminders
* Support one-time and recurring reminders with cron-like scheduling
* Deliver reminders via email, Telegram, and ntfy.sh push notifications
* Expose NLA endpoints for natural-language reminder creation

## Architecture

`NotifService` runs a tick-based loop that:

1. Scans the reminder directory for JSON reminder files
2. Checks each reminder's trigger conditions against the current time
3. Fires due reminders through configured delivery channels
4. Prunes expired one-time reminders automatically

Reminders are stored as individual JSON files in the configured `reminder_dir`.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/reminder/create` | Create a new reminder (saves to disk) |
| `POST` | `/reminder/delete` | Delete a reminder by ID |

## NLA Endpoints

| Name | Description |
|------|-------------|
| `create_reminder` | Create a reminder given a message and a time |

The NLA endpoint uses the LLM to parse natural-language reminder requests (e.g., "remind me to buy groceries tomorrow at 5pm") into structured reminder definitions, with configurable retry attempts.

## Reminder Model

**`Reminder` Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | `str` | Yes | Reminder message text |
| `title` | `str` | No | Reminder title (default: `"Reminder"`) |
| `send_telegrams` | `list` | No | Telegram chat targets for delivery |
| `send_emails` | `list` | No | Email addresses for delivery |
| `send_ntfys` | `list` | No | ntfy.sh topics for delivery |
| `trigger_years` | `list` | No | Years to fire (e.g., `[2025]`) |
| `trigger_months` | `list` | No | Months to fire, 1–12 |
| `trigger_days` | `list` | No | Days of month to fire, 1–31 (negative = from end) |
| `trigger_weekdays` | `list` | No | Weekdays to fire, Sunday=1 through Saturday=7 |
| `trigger_hours` | `list` | No | Hours to fire, 0–23 |
| `trigger_minutes` | `list` | No | Minutes to fire, 0–59 |
| `id` | `str` | No | Unique reminder ID (auto-generated if omitted) |

Reminders support flexible scheduling:

* **One-time:** Fires at a specific date and time, then is auto-pruned
* **Recurring:** Fires on matching conditions repeatedly

Trigger conditions are specified as discrete lists of matching values:

* `trigger_years` — Specific years (e.g., `[2025, 2026]`)
* `trigger_months` — Specific months, 1–12 (e.g., `[1, 6, 12]`)
* `trigger_days` — Specific days of the month, 1–31; negative values count from end of month (e.g., `[-1]` for last day)
* `trigger_weekdays` — Specific weekdays, Sunday=1 through Saturday=7
* `trigger_hours` — Specific hours, 0–23 (e.g., `[9, 10, 11]`)
* `trigger_minutes` — Specific minutes, 0–59

## Delivery Channels

| Channel | Mechanism |
|---------|-----------|
| **Telegram** | Calls Telegram service's `/bot/send/message` endpoint via `OracleSession` |
| **Email** | Sends via `Messenger` (IFTTT webhook) |
| **ntfy.sh** | Push notification via `NtfyChannel` |

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `reminder_dir` | `str` | Directory containing reminder JSON files |
| `messenger_webhook_event` | `str` | IFTTT webhook event for email delivery |
| `webhook_key` | `str` | IFTTT webhook API key |
| `dialogue` | `DialogueConfig` | OpenAI settings for NLA parsing |
| `telegram` | `OracleSessionConfig` | Connection to Telegram service |
| `nla_create_reminder_dialogue_retries` | `int` | LLM retries for NLA reminder parsing |

## Dependencies

* **Library modules:** `lib.mail`, `lib.ntfy`, `lib.dialogue`, `lib.oracle`, `lib.service`, `lib.ifttt`
* **Other services:** Telegram (for message delivery via OracleSession)
* **External services:** IFTTT (for email delivery), ntfy.sh (for push notifications)
