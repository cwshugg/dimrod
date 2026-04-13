# Gatekeeper — Event Dispatcher

Gatekeeper is an event routing service that receives event notifications and dispatches subscriber scripts to handle them.

## Purpose

* Accept event posts from external sources (e.g., IFTTT, motion sensors, cron jobs)
* Dispatch configured subscriber scripts when events fire
* Run subscribers in separate threads with timeout control

## Architecture

`GatekeeperService` loads event configurations at startup. Each event defines one or more subscribers — Python scripts that are spawned as subprocesses when the event fires.

When an event is posted via the `/events/post` endpoint:

1. The service finds all events matching the posted name
2. For each matching event, it creates a `threading.Thread` that calls `asyncio.run(event.fire(...))`
3. Inside `fire()`, each subscriber's `spawn()` method launches the script as a subprocess via `subprocess.Popen`
4. Subscriber stdout/stderr is captured and logged
5. Thread count is limited by `thread_limit`; when the pool is full, threads are joined with the configured `thread_timeout`

## Oracle Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/events/get` | List all configured events and their subscribers |
| `POST` | `/events/post` | Post an event to trigger its subscribers |

### `/events/post` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Event name to fire |
| `data` | No | Arbitrary data passed to subscribers |

## NLA Endpoints

None.

## Subscriber Scripts

Subscriber scripts are standalone Python executables that run when their parent event fires. Existing subscribers include:

| Script | Trigger Event | Action |
|--------|---------------|--------|
| `motion_outside_front.py` | Front door motion | Turn on front porch lights via Lumen |
| `motion_outside_back.py` | Back door motion | Turn on back porch lights via Lumen |
| `status_report.py` | Status report request | Email a system status report |

Subscribers communicate with other DImROD services via `OracleSession`.

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `events` | `list[GatekeeperEventConfig]` | Event definitions |
| `thread_limit` | `int` | Maximum concurrent subscriber threads |
| `thread_timeout` | `int` | Thread join timeout when the pool is full (seconds). Default: `0.01` — this effectively means threads are polled without blocking, allowing the service to cycle quickly through active threads |

### Event Configuration

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Event name (matched against posts) |
| `subscribers` | `list[GatekeeperSubscriberConfig]` | Scripts to run |

### Subscriber Configuration

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Subscriber name |
| `executable` | `str` | Path to the subscriber script |

## Dependencies

* **Library modules:** `lib.oracle`, `lib.service`
* **Other services:** Lumen (called by motion subscribers), Telegram (called by some subscribers)
* **External triggers:** IFTTT webhooks, motion sensors, cron jobs
