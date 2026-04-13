# Taskmaster — Job Scheduler

Taskmaster is an automated task runner that dynamically loads and executes recurring Python job scripts.

## Purpose

* Dynamically discover and load task job scripts from the `tasks/` directory
* Schedule and execute jobs based on configurable intervals
* Run jobs concurrently using a thread pool
* Integrate with Todoist, Google Calendar, YNAB, Garmin, and other external services

## Architecture

`TaskmasterService` uses a producer-consumer pattern:

1. **Discovery:** On startup, it dynamically imports all Python modules from `tasks/` and discovers classes that extend `TaskJob`
2. **Scheduling:** Each tick, it checks which jobs are due to run (based on `get_next_update_datetime()` and timestamp tracking)
3. **Execution:** Due jobs are placed on a queue and processed by a pool of `TaskmasterThread` worker threads
4. **Persistence:** Each job persists its last update and last success timestamps to pickle files (`.taskjob_*_last_update.pkl`, `.taskjob_*_last_success.pkl`)

### TaskJob Base Class

Every task extends `TaskJob`, which defines:

| Method | Description |
|--------|-------------|
| `init()` | Optional initialization hook, called before any `update()` calls |
| `update(todoist, gcal)` | The job's main logic — must return `True` on success (override this) |
| `get_name()` | Returns the task job's name (derived from the class name) |
| `get_id()` | Returns a unique identifier for the task job instance |
| `get_next_update_datetime()` | Calculates when the job should next run, based on `refresh_rate` |
| `get_last_update_datetime()` | Timestamp of the last execution attempt (loaded from disk) |
| `get_last_success_datetime()` | Timestamp of the last successful execution (loaded from disk) |

## Task Categories

Jobs are organized under `tasks/` by domain:

| Category | Tasks | External Services |
|----------|-------|-------------------|
| **Finance** | YNAB budget auto-categorization, transaction reports | YNAB, Speaker (LLM) |
| **Garmin** | Health data sync (steps, sleep, heart rate, activities) | Garmin Connect |
| **Groceries** | Grocery list management | Todoist |
| **Household** | Recurring household tasks | Todoist, Google Calendar |
| **Automotive** | Vehicle maintenance tasks | Todoist, Google Calendar |
| **Medical** | Medical appointment tasks | Todoist, Google Calendar |
| **Family** | Family-related tasks | Todoist, Google Calendar |
| **LifeTracker** | Personal metrics tracking via interactive menus | Telegram, SQLite |
| **News** | Weekly headline reports | NewsAPI, Telegram |
| **Services** | Wyze API key regeneration | Wyze |
| **System** | Downtime detection and alerting | (local) |
| **Interview** | Interview preparation tasks | Todoist |
| **Wedding** | Wedding planning and countdown tasks | Todoist, Google Calendar |

## Oracle Endpoints

None — Taskmaster has no HTTP API endpoints (marked as TODO in the codebase).

## NLA Endpoints

None.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `todoist` | `TodoistConfig` | Todoist API credentials |
| `google_calendar` | `GoogleCalendarConfig` | Google Calendar credentials |
| `refresh_rate` | `int` | Tick interval (seconds) |
| `worker_threads` | `int` | Number of concurrent worker threads |
| `lumen` | `OracleSessionConfig` | Connection to Lumen service |
| `telegram` | `OracleSessionConfig` | Connection to Telegram service |
| `speaker` | `OracleSessionConfig` | Connection to Speaker service |
| `dialogue` | `DialogueConfig` | OpenAI settings for LLM-powered tasks |
| `wyze` | `WyzeConfig` | Wyze account credentials |

## Dependencies

* **Library modules:** `lib.todoist`, `lib.google.google_calendar`, `lib.oracle`, `lib.dialogue`, `lib.wyze`, `lib.service`
* **Other services:** Speaker (LLM operations), Telegram (user interaction, menus)
* **External APIs:** Todoist, Google Calendar, YNAB, Garmin Connect, NewsAPI, Wyze

## Notable Details

* Tasks are discovered at runtime via dynamic import — adding a new task is as simple as creating a new Python file in `tasks/`
* The thread pool uses a `TaskmasterThreadQueue` with `TaskmasterThreadQueueEntry` objects (each containing a `TaskJob` and a `TaskmasterThreadQueueFuture`). The future provides `wait()` and `mark_complete()` methods using a condition variable, allowing the service to block until a job finishes.
* Pickle-based timestamp persistence survives service restarts
* The YNAB auto-categorization task uses Speaker's LLM to classify uncategorized transactions
* The LifeTracker tasks use Telegram's interactive menu system for user data entry
