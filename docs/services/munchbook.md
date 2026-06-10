# Munchbook — Food Diary

Munchbook is a per-user food logging service that maintains SQLite databases of food entries. Each configured user has their own database, and access is controlled by mapping Oracle auth usernames to user databases.

## Purpose

* Track food consumption over time for medical, fitness, or personal reasons
* Store timestamped food entries with descriptions and notes
* Provide search and retrieval of entries within time ranges
* Support multiple users, each with their own isolated database
* Enforce per-database access control via Oracle authentication

## Architecture

`MunchbookService` manages a collection of `MunchbookDatabase` instances — one per configured user. The service itself has no background logic; all functionality is exposed through Oracle endpoints. Each database is thread-safe via a lock in `MunchbookDatabase`.

All timestamps are stored as UTC epoch seconds. Conversion to UTC is performed in `MunchbookEntry.parse_json()` using `datetime.utcfromtimestamp()`, and conversion back uses `calendar.timegm()`.

### Access Control

Each configured user has an `auth_usernames` list that specifies which Oracle auth users may read from and write to that user's database. Endpoints check access before performing any operation, and the user list endpoint filters out inaccessible databases.

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/users/list` | List users accessible to the authenticated user |
| `POST` | `/entries/search` | Search entries by time range |
| `POST` | `/entries/add` | Add a new food entry |

### `/users/list` Response

Returns a list of user objects the authenticated Oracle user has access to:

```json
{
    "success": true,
    "payload": [
        {"name": "cwshugg"},
        {"name": "rcbullins"}
    ]
}
```

### `/entries/search` Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_name` | `str` | Yes | Name of the configured user whose database to search |
| `start` | `int` | Yes | Start of time range (UTC epoch seconds) |
| `end` | `int` | Yes | End of time range (UTC epoch seconds) |
| `count` | `int` | No | Maximum number of entries to return |

Returns entries as a JSON list, each including an `entry_id`.

### `/entries/add` Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_name` | `str` | Yes | Name of the configured user whose database to write to |
| `description` | `str` | Yes | Natural language description of the food eaten |
| `notes` | `str` | No | General notes (not necessarily food-related) |
| `timestamp` | `int` | Yes | When the food was eaten (UTC epoch seconds) |

Returns the generated `entry_id` in the payload on success.

## NLA Endpoints

None.

## Entry Data Model

`MunchbookEntry`:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `int` (converted to `datetime`) | When the food was eaten. Stored as UTC epoch seconds in JSON/SQLite; converted to a `datetime` object in `parse_json()` |
| `description` | `str` | Natural language description of the food eaten |
| `notes` | `str` | General-purpose notes (default: empty string) |

Entry IDs are SHA-256 hashes generated from the entry content plus random salt.

## Configuration

### Service-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `users` | `list` | Yes | List of user configuration objects |

### Per-User Fields (`MunchbookUserConfig`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Display name and identifier for this user |
| `db_path` | `str` | Yes | Path to this user's SQLite database file |
| `auth_usernames` | `list[str]` | Yes | Oracle auth usernames allowed to access this database |

### Example Configuration

```yaml
service_name: munchbook
service_log: stdout
msghub_name: munchbook
oracle:
  addr: 0.0.0.0
  port: 2370
  log: stdout
  auth_cookie: munchbook_auth
  auth_secret: YOUR_JWT_SECRET_HERE
  auth_users:
  - username: my_user
    password: my_password
    privilege: 0
  - username: __telegram
    password: YOUR_TELEGRAM_PASSWORD_HERE
    privilege: 1
users:
- name: alice
  db_path: ./.munchbook_alice.db
  auth_usernames:
  - my_user
  - __telegram
```

## Telegram Integration

Munchbook is accessible via the Telegram bot using the `/foodlog` command (aliases: `/food`, `/f`, `/munchbook`).

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/foodlog` | List accessible Munchbook users |
| `/foodlog list` | List accessible Munchbook users |
| `/foodlog recent [user_name] [count]` | Show recent food entries (default: 10) |
| `/foodlog entry. DESCRIPTION. NOTES` | Quick-add using the message timestamp |
| `/foodlog entry. YYYY-MM-DD HH:MM. DESCRIPTION. NOTES` | Quick-add with a 24-hour datetime |
| `/foodlog entry. 1:30pm. DESCRIPTION. NOTES` | Quick-add with a 12-hour time (today) |
| `/foodlog search <user> <start> <end> [count]` | Search entries by Unix timestamp range |
| `/foodlog help` | Show usage help |

### Auto-Detection

When using `/foodlog entry.` or `/foodlog recent` without specifying a user, the bot auto-detects the target database by matching the Telegram chat name against configured Munchbook user names (case-insensitive substring match, minimum 3 characters).

### Telegram Configuration

The Telegram service requires a `munchbook` Oracle session config block (optional — the bot starts without it but the `/foodlog` command will report that Munchbook is not configured):

```yaml
munchbook:
  addr: 0.0.0.0
  port: 2370
  auth_username: __telegram
  auth_password: YOUR_TELEGRAM_PASSWORD_HERE
```

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`, `lib.config`, `lib.uniserdes`, `lib.dtu` (Telegram command only)
* **Other services:** None (Munchbook is called by others, primarily Telegram)

## Notable Details

* Each user's database is fully isolated — there is no cross-user querying
* The database layer uses parameterized queries to prevent SQL injection
* All database operations are wrapped in `try/finally` to prevent lock deadlocks on failure
* The `search()` method gracefully handles `count <= 0` by returning an empty list
* The `/entries/search` endpoint validates that `start <= end`
