# Historian — Event Archive

Historian is a simple event storage service that archives events submitted by other services into a SQLite database.

## Purpose

* Accept and store events from any DImROD service
* Provide retrieval of events by ID or by recency
* Serve as a long-term audit log for the system

## Architecture

`HistorianService` manages a SQLite database with a single `events` table. The service itself has minimal background logic — most functionality is exposed through Oracle endpoints. Thread-safe access is ensured via a lock.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/submit` | Submit an event for storage |
| `POST` | `/retrieve/latest` | Retrieve the N most recent events |
| `POST` | `/retrieve/by_id` | Retrieve a specific event by its ID |

### `/submit` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `author` | Yes | Who/what submitted the event |
| `title` | Yes | Event title |
| `description` | No | Detailed description |
| `timestamp` | No | Event timestamp (auto-generated if omitted) |
| `tags` | No | List of tags for categorization |

### `/retrieve/latest` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `count` | Yes | Number of recent events to retrieve |

### `/retrieve/by_id` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `event_id` | Yes | Unique event identifier |

## NLA Endpoints

None.

## Event Data Model

`HistorianEvent`:

| Field | Type | Description |
|-------|------|-------------|
| `author` | `str` | Event source |
| `title` | `str` | Event title |
| `description` | `str` | Event details |
| `timestamp` | `int` (converted to `datetime`) | When the event occurred. Stored as `int` (epoch seconds) in Uniserdes; converted to `datetime` in `parse_json()` |
| `tags` | `list[str]` | Categorization tags |

Event IDs are SHA-based hashes generated from the event content.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `db_path` | `str` | Path to the SQLite database file |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`, `lib.db`
* **Other services:** None (Historian is called by others)

## Notable Details

* The database layer (`db.py` in the Historian directory) provides search functions by ID, timestamp, author, title, and tags
* Historian is a passive service — it only stores what other services submit
