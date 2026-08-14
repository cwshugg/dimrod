# Membank — Memory-Bank Service

Membank stores plaintext "memories" (short notes and ideas) in one or more
**memory banks**, each backed by its own SQLite database. It exposes an
authenticated, ACL-guarded HTTP API for creating, retrieving, updating,
deleting, and filtering memories, plus a natural-language layer that lets users
save and recall notes conversationally through the [Telegram](telegram.md) bot.

## Purpose

* Persist plaintext notes ("memories") across multiple isolated banks
* Guard every bank with per-bank read/write access-control lists (ACLs)
* Provide an explicit, "dumb" HTTP API that receives only concrete values
* Keep all natural-language understanding in a dedicated NLA layer — never in
  the HTTP API
* Offer a single Telegram `/memory` command that both **stores** and **recalls**
  notes, choosing intent automatically

A key design principle: the HTTP API is deliberately explicit. It never guesses
intent, parses prose, or calls an LLM. All natural-language interpretation lives
in the [NLA layer](#natural-language-actions-nla), which converts free-form text
into the explicit values the API expects.

## Architecture

`MembankService` keeps its main service thread lightweight — after validating
config, resolving/creating each bank's database, and starting the worker pool,
it simply sleeps in a loop. All real work happens in the Oracle HTTP endpoints
and the [worker-thread pool](#concurrency--locking).

Each bank is an **independent SQLite file**. This gives each bank a clean
isolation and ACL boundary: a memory `id` can never cross a bank boundary, and a
bank is resolved only by its configured `id` (clients never supply a file path).

### Key components

| Component | File | Responsibility |
|-----------|------|----------------|
| `MembankService` | `membank.py` | Config, bank registry, worker pool, NLA extraction helpers |
| `MembankOracle` | `membank.py` | HTTP endpoints, auth, per-bank ACL enforcement |
| `MemoryBank` / `MemoryBankRegistry` | `models.py` | Per-bank SQLite operations, tag index, id/path uniqueness |
| `Memory` | `models.py` | The note data model (persisted via `Uniserdes`) |
| `WorkerPool` / `WorkerThread` / `Job` / `WorkerPoolConfig` | `threads.py` | Bounded thread pool that serializes DB work (+ its config) |
| NLA layer | `nla.py` | The **only** place LLM logic lives (store/recall extraction) |

## Concepts

### Memory bank

A **memory bank** (`MemoryBank`) is one SQLite database file with:

* A unique `id` — the client-facing handle used to select the bank. Must match
  `^[A-Za-z0-9_-]+$` (this doubles as a path-safety constraint). A bank `id` is
  **never** used to build a filesystem path.
* A human-readable `name` (display only; need not be unique).
* A `db_path` — the SQLite file. This value is **authoritative**: it comes from
  trusted admin config, so there is no `db_dir` containment anchor. Each bank is
  defined solely by its own `db_path`. The **parent directory of `db_path` is
  auto-created** at startup (`os.makedirs(dirname, exist_ok=True)`) so the SQLite
  file can be created in the configured location.
* Two ACL user lists: `read_users` and `write_users`, drawn from the Oracle's
  `auth_users`.
* Its own in-process [`ReadWriteLock`](#concurrency--locking).

A `MemoryBank` is **config-driven**: it is constructed from a single
`MemoryBankConfig` (defined in `models.py`) and references every per-bank
attribute through `self.config` (`config.id`, `config.name`, `config.db_path`,
`config.read_users`, `config.write_users`, `config.lock_timeout`) — a single
source of truth.

Bank `id` and resolved `db_path` uniqueness are enforced when the
`MemoryBankRegistry` is built at startup. Each `db_path` is `realpath`-normalized
and no two banks may resolve to the same file. A duplicate `id`, a duplicate
resolved `db_path`, or an invalid `id` is a **fatal config error** — the service
refuses to start.

### Memory

A **memory** (`Memory`) is a single note. Its fields:

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | UUID4 hex, generated on creation |
| `timestamp` | `int` | Unix epoch seconds (defaults to "now" if omitted) |
| `name` | `str` | Short title; non-empty, ≤ 256 characters |
| `content` | `str` | The note body; non-empty, ≤ 64 KiB |
| `tags` | `list[str]` | Sanitized tags (see below); ≤ 32 per memory |

Over the API, `tags` is always a JSON list. Internally the memory row stores
tags as a canonical JSON-array string; the normative, queryable tag *index*
lives in the per-bank `tags` / `memory_tags` tables.

### Tags

Tags are normalized and validated before storage. Each tag is:

1. **Trimmed** of surrounding whitespace.
2. **Lowercased.**
3. Validated against `^[A-Za-z_][A-Za-z0-9_-]*$` — it must start with a letter
   or underscore, then contain only letters, digits, underscores, or hyphens.

Limits enforced during sanitization:

* A single tag may be at most **64 characters** (after trimming).
* A memory may have at most **32 distinct tags**.

Duplicate tags collapse to one. Any tag that fails validation causes the write
to be rejected with a `400`. Tags supplied in query filters are sanitized the
same way before matching.

### Tag index and rebuild

Each bank maintains a derived, refcounted tag index in two tables:

* `tags` — `(tag TEXT PRIMARY KEY, refcount INTEGER)`. The `refcount` is how many
  memories currently carry that tag; a tag whose refcount reaches zero is
  pruned.
* `memory_tags` — `(memory_id, tag)` membership rows.

The index is updated incrementally on every add/update/delete. Two safeguards
keep it consistent:

* **Self-heal on startup:** during schema init, if a bank has memories but an
  empty tag index (e.g. a freshly migrated DB), the index is rebuilt from the
  memory rows automatically.
* **Manual rebuild:** the [`/bank/rebuild_tags`](#post-bankrebuild_tags)
  admin endpoint recomputes the entire index from scratch.

## Configuration

Membank follows the standard DImROD config convention: a real, git-ignored
`cwshugg_membank.yaml` alongside a redacted `membank.yaml` template. The config
extends `ServiceConfig` (which requires `service_name`, `msghub_name`, and an
`oracle` block with `auth_users`).

### Service fields

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `banks` | `list[MemoryBankConfig]` | ✓ | — | All configured memory banks and their ACLs |
| `worker_pool` | `WorkerPoolConfig` | ✗ | defaults | Nested worker-pool settings (`worker_count`, `max_queue_size`); omit to use the defaults below |
| `lock_timeout` | `float`/`int` | ✗ | `10.0` | **Service-level default** per-bank `ReadWriteLock` acquire timeout, in seconds. On timeout the request fails secure with a retryable `503`. `null` or `0` means an unbounded wait (the historical behavior). Each bank inherits this default unless it sets its own `lock_timeout` |
| `dialogue` | `DialogueConfig` | ✗ | `None` | LLM settings used **only** by the NLA layer; omit to run the core API without NLA |

### Worker-pool fields (`worker_pool` → `WorkerPoolConfig`)

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `worker_count` | `int` | ✗ | `4` | Number of DB worker threads in the pool |
| `max_queue_size` | `int` | ✗ | `128` | Upper bound on DB jobs waiting for a free worker. When the queue is full, `submit()` rejects the request with a retryable `503` instead of queueing without limit. `0` means an unbounded queue (the historical behavior) |

### Per-bank fields (`MemoryBankConfig`)

| Field | Type | Required | Description |
|-------|------|:--------:|-------------|
| `id` | `str` | ✓ | Unique bank handle; must match `^[A-Za-z0-9_-]+$` |
| `name` | `str` | ✓ | Human-readable title (display only) |
| `db_path` | `str` | ✓ | Authoritative SQLite file path; its parent directory is auto-created at startup |
| `read_users` | `list[str]` | ✓ | Oracle usernames allowed to read/list/query the bank |
| `write_users` | `list[str]` | ✓ | Oracle usernames allowed to add/update/delete in the bank |
| `lock_timeout` | `float`/`int` | ✗ | Optional per-bank override of the service-level `lock_timeout`; when unset, inherits the service default |

The Oracle listens on port **2380** by default.

### Path handling

Bank `db_path`s come from **trusted admin config**, so there is no `db_dir`
containment anchor — each `db_path` is authoritative. The retained safeguards
are: each `db_path` is `realpath`-normalized and no two banks may resolve to the
same file (a duplicate resolved `db_path` is a **fatal config error**); bank
`id`s must be unique and are **never** used to construct filesystem paths. The
parent directory of each `db_path` is auto-created at startup
(`os.makedirs(dirname, exist_ok=True)`).

### Concurrency knobs

Three knobs tune the shared [worker pool](#concurrency--locking) and per-bank
locking:

* `worker_pool.worker_count` (default `4`) sizes the pool of DB worker threads.
* `lock_timeout` (default `10.0`, seconds) bounds how long a request waits to
  acquire a bank's per-bank `ReadWriteLock`. If the lock cannot be acquired in
  time, the request fails secure with a retryable `503` rather than blocking a
  worker indefinitely. Set it to `null` or `0` for an unbounded wait (the
  historical behavior).
* `worker_pool.max_queue_size` (default `128`) bounds how many DB jobs may wait
  for a free worker. When the pool is saturated the request is rejected
  immediately with a retryable `503` instead of being queued without limit, so
  one hot bank cannot exhaust shared capacity. Set it to `0` for an unbounded
  queue (the historical behavior).

Both `503` paths are fail-secure: see [Concurrency & Locking](#concurrency--locking)
for details, including the guarantee that a timed-out mutation applies **no**
partial write.

### Example configuration

A sanitized `membank.yaml` (all secrets replaced with placeholders):

```yaml
service_name: membank
service_log: stdout
msghub_name: YOUR_MSGHUB_NAME
oracle:
  addr: 0.0.0.0
  port: 2380
  log: stdout
  auth_cookie: membank_auth
  auth_secret: YOUR_JWT_SECRET_HERE
  auth_users:
    - { username: admin,      password: YOUR_PASSWORD_HERE, privilege: 0 }
    - { username: __telegram, password: YOUR_PASSWORD_HERE, privilege: 1 }
    - { username: __speaker,  password: YOUR_PASSWORD_HERE, privilege: 1 }

# Worker-thread pool settings (nested). worker_count sizes the DB worker pool;
# max_queue_size bounds the queue (shedding load with a retryable 503 when full).
# Omit this block to use the defaults (4 workers / queue of 128).
worker_pool:
  worker_count: 4
  max_queue_size: 128

# Fail-secure availability knob. lock_timeout (seconds) is the SERVICE-LEVEL
# default that caps how long a request waits for a bank's read/write lock before
# shedding load with a retryable 503. Each bank inherits it unless it sets its
# own lock_timeout. Use null/0 for the unbounded (historical) behavior.
lock_timeout: 10

# LLM config used ONLY by the NLA layer. Omit to run without NLA endpoints.
dialogue:
  openai_api_key: YOUR_OPENAI_API_KEY_HERE
  openai_chat_model: gpt-4o-mini

banks:
  - id: personal
    name: "Personal notes"
    db_path: /var/lib/dimrod/membank/db/personal.db
    read_users:  [admin, __speaker, __telegram]
    write_users: [admin, __speaker, __telegram]
  - id: worldbuilding
    name: "Shared worldbuilding ideas"
    db_path: /var/lib/dimrod/membank/db/worldbuilding.db
    read_users:  [admin, __speaker, __telegram]
    write_users: [admin, __speaker, __telegram]
  - id: events
    name: "Automated event log"
    db_path: /var/lib/dimrod/membank/db/events.db
    # Event-source services write; humans read (least privilege).
    read_users:  [admin, __speaker, __telegram]
    write_users: [admin]
```

### Telegram per-chat bank mapping

Membank itself has **no** default-bank concept — it only ever receives an
explicit bank `id`. Choosing which bank a given Telegram chat targets is a
telegram-side concern. Each entry in the Telegram service's `bot_chats` list may
carry an optional `memory_bank` field naming the target bank:

```yaml
bot_chats:
  - id: "123456789"
    memory_bank: personal
  - id: "987654321"
    memory_bank: worldbuilding
```

When a chat runs `/memory`, the Telegram service resolves that chat's
`memory_bank` (via `get_chat_memory_bank`) and uses it as the **default** bank
whenever a subcommand's bank field is left empty or `-`. A bank named explicitly
as the first field of a subcommand overrides this default. If a chat has no
`memory_bank` and no bank is named, the command replies with a clear error
asking the user to specify a bank or configure a default.

For the natural-language (non-slash, conversational) path, the resolved default
bank is instead attached to the Speaker's NLA request; see
[Natural-Language Actions](#natural-language-actions-nla).

## Oracle Endpoints

All endpoints use `POST`, require an authenticated Oracle session, and return
the standard Oracle JSON envelope:

```json
{ "success": true, "message": "...", "payload": { ... } }
```

The tables below describe the contents of `payload` on success, and the
request JSON each endpoint reads. Every endpoint enforces its ACL **server-side**
using the authenticated caller's username.

### `POST /bank/list`

Lists the banks the caller may read (ACL-filtered). No `bank` field required.

* **Auth:** any authenticated user.
* **Request:** *(none)*
* **Response payload:**

```json
{
  "banks": [
    { "id": "personal", "name": "Personal notes", "can_write": true, "memory_count": 12 }
  ]
}
```

### `POST /memory/list`

Returns a filtered, paginated page of memories.

* **Auth / ACL:** read access to `bank`.
* **Request:**

```json
{
  "bank": "personal",
  "filters": {
    "time_range": { "start": 1700000000, "end": 1701000000 },
    "tags": ["car", "parking"],
    "tag_mode": "any",
    "keyword": "section g7"
  },
  "limit": 100,
  "offset": 0,
  "order": "desc"
}
```

All `filters` are optional and combined with `AND`. `tag_mode` is `"any"`
(default) or `"all"`; `keyword` is a substring matched against `name` +
`content`. `limit` defaults to 100 and is hard-capped at 500. `order` is `"desc"`
(newest-first, default) or `"asc"`.

* **Response payload:**

```json
{
  "bank": "personal",
  "count": 1,
  "total": 12,
  "memories": [
    { "id": "ab12...", "timestamp": 1700500000, "name": "Parking spot",
      "content": "Parked in section G7", "tags": ["car", "parking"] }
  ]
}
```

`count` is the number of memories on this page; `total` is the full match count
ignoring pagination.

### `POST /memory/get`

Fetches a single memory by `id`.

* **Auth / ACL:** read access to `bank`.
* **Request:** `{ "bank": "personal", "id": "ab12..." }`
* **Response payload:** `{ "memory": { ...memory fields... } }`
* Returns `404` if the memory does not exist in that bank.

### `POST /memory/add`

Creates a memory (write).

* **Auth / ACL:** write access to `bank`.
* **Request:**

```json
{
  "bank": "personal",
  "name": "Parking spot",
  "content": "Parked in section G7",
  "tags": ["car", "parking"],
  "timestamp": 1700500000
}
```

`tags` defaults to `[]`; `timestamp` defaults to the current time. `name` and
`content` are validated and `tags` sanitized server-side.

* **Response payload:** `{ "id": "ab12...", "bank": "personal" }`

### `POST /memory/update`

Modifies an existing memory (write). Only fields present in the request are
changed.

* **Auth / ACL:** write access to `bank`.
* **Request:** `{ "bank": "personal", "id": "ab12...", "name": "...", "content": "...", "tags": [...], "timestamp": 123 }`
* **Response payload:** `{ "id": "ab12...", "updated": true }`
* Returns `404` if the memory does not exist in that bank.

### `POST /memory/delete`

Deletes a memory by `id` (write).

* **Auth / ACL:** write access to `bank`.
* **Request:** `{ "bank": "personal", "id": "ab12..." }`
* **Response payload:** `{ "id": "ab12...", "deleted": true }`
* Returns `404` if the memory does not exist in that bank.

### `POST /tag/list`

Lists a bank's tags with their usage counts.

* **Auth / ACL:** read access to `bank`.
* **Request:** `{ "bank": "personal" }`
* **Response payload:**

```json
{
  "bank": "personal",
  "tags": [ { "tag": "car", "count": 4 }, { "tag": "parking", "count": 2 } ]
}
```

Tags are ordered by count (descending), then alphabetically.

### `POST /bank/rebuild_tags`

Rebuilds a bank's tag index from its memory rows. This is an administrative
maintenance operation.

* **Auth / ACL:** requires an **admin** user (`privilege == 0`) **and** write
  access to `bank`. Non-admin callers receive `404` (the endpoint's existence is
  never confirmed).
* **Request:** `{ "bank": "personal" }`
* **Response payload:** `{ "bank": "personal", "rebuilt": true }`

### Error and status conventions

| Status | Meaning |
|:------:|---------|
| `400` | Invalid input (`MembankInputError`) — e.g. an empty `name`, oversized `content`, or a tag that fails validation |
| `403` | The bank is readable but not writable by the caller (write endpoints only) |
| `404` | Missing/unknown bank, unknown memory, or an unauthorized caller — the bank's existence is deliberately hidden from users who cannot read it |
| `503` | Fail-secure "service busy" (retryable) — returned when the worker pool queue is saturated (`WorkerPoolSaturated`) or a bank lock cannot be acquired within `lock_timeout` (`MembankLockTimeout`); a timed-out mutation applies **no** partial write (see [Concurrency & Locking](#concurrency--locking)) |
| `500` | Unexpected server error; the message is generic (no stack traces, SQL, or memory content is leaked) |

A subtle but important detail: unauthorized access to a bank returns `404`, not
`403`, so an attacker cannot use the status code to learn that a bank exists.
The `403` case is reserved for the "you can read this bank but not write to it"
distinction.

## Natural-Language Actions (NLA)

The NLA layer (`nla.py`) is the **only** place in Membank where LLM logic lives.
It registers two endpoints that the [Speaker](speaker.md) discovers via
`/nla/get` and routes matching utterances to. Each handler performs the same
server-side per-bank ACL as the regular endpoints, using the service account
that invoked the NLA endpoint.

| NLA endpoint | Handler | Purpose |
|--------------|---------|---------|
| `remember` | `nla_remember` | Store a note: turns "remember this: ..." into explicit `{name, content, tags, bank?}` and calls `add_memory` under the write ACL |
| `recall` | `nla_recall` | Query notes: turns a question into structured filters `{tags, tag_mode, time_range, keyword, bank?}` (the current UTC time is injected so phrases like "last month" resolve) and lists matches |

The Speaker's router decides store-vs-query intent by selecting one of these two
endpoints. Membank never guesses: the LLM extraction happens here, and the
resulting explicit values flow into the same "dumb" bank operations the HTTP API
uses. A bank explicitly named in the utterance overrides the telegram-supplied
default; if neither is available, the handler returns a "which bank?"
clarification.

## Telegram `/memory` Command

The [Telegram](telegram.md) bot's `/memory` command (alias: `/m`) is the primary
user interface to Membank. A single command handles **both** storing and
recalling — the user never has to say which they mean.

How it works:

1. Telegram resolves the chat's configured target bank
   (`get_chat_memory_bank` → the chat's `memory_bank`).
2. The free-form text plus the resolved default bank are forwarded to the
   Speaker (`memory_talk`), which routes the utterance to Membank's NLA layer.
3. The NLA layer decides store-vs-query, performs the operation, and returns a
   reply, which Telegram renders as HTML.

Telegram performs no natural-language parsing of its own; it only enforces the
chat whitelist, attaches the target bank id, and renders the reply.

### Examples

Storing:

```text
/memory remember that I parked in section G7
/m here's a worldbuilding idea: the northern glaciers hide an old city
```

Recalling:

```text
/memory what did I save about magic systems?
/m what did I tell you last month about the car?
```

### Default bank and inline override

* The chat's `memory_bank` (from `bot_chats`) is the **default** target bank.
* A bank named explicitly in the message text **overrides** the default.
* If the chat has no configured bank and none is named, the NLA layer replies
  asking which bank to use.
* Sending `/memory` with no text shows usage help, including the chat's currently
  configured bank.

## Concurrency & Locking

Membank protects SQLite with two coordinated mechanisms:

* **A fixed worker pool** (`WorkerPool`, `worker_pool.worker_count` threads,
  default 4). Every
  Oracle handler builds a `Job` (a callable plus its arguments and a completion
  event), submits it to a shared, thread-safe queue, and **blocks on the
  result**. A worker pops the job, runs the DB operation, and signals completion.
  This bounds how many threads touch SQLite at once, while the gevent WSGI server
  still accepts many simultaneous HTTP connections. (If the pool has not been
  started — e.g. in tests — the callable runs inline on the calling thread.)
* **A per-bank `ReadWriteLock`.** Each `MemoryBank` owns exactly one lock.
  Read operations acquire it for shared (reader) access; writes acquire it
  exclusively. Correctness rests on this lock, not on the pool size. Each DB
  method opens a short-lived SQLite connection (WAL journaling, foreign keys on)
  *inside* the lock's critical section and closes it when done; no connection is
  cached or shared across threads.

Both mechanisms are bounded so that overload sheds load with a fail-secure,
retryable `503` instead of stalling indefinitely:

* **Lock-acquire timeout.** Each per-bank `ReadWriteLock` acquisition is bounded
  by `lock_timeout` (default `10.0` seconds). If a reader or writer cannot
  acquire the lock in time, the operation raises `MembankLockTimeout`, which the
  Oracle maps to a `503`. Because the timeout is applied **before** the DB
  connection is opened and any SQL runs, a timed-out mutation applies **no
  partial write** — the request simply never touched the database. Set
  `lock_timeout` to `null` or `0` to restore the historical unbounded wait.
* **Bounded work queue.** The `WorkerPool` queue holds at most
  `worker_pool.max_queue_size` jobs (default `128`). When the queue is full,
  `WorkerPool.submit()` rejects the
  job immediately with `WorkerPoolSaturated` (also mapped to a `503`) rather than
  queueing without bound, so a burst against one hot bank cannot exhaust shared
  capacity for unrelated banks. Set `max_queue_size` to `0` for an unbounded
  queue (the historical behavior).

Both `503` responses are retryable: clients should back off and retry. See the
[error and status conventions](#error-and-status-conventions) table for the full
status code list.

## Security Notes

Membank's security posture (validated in a dedicated security review — no
critical findings) rests on:

* **Server-side ACL enforcement.** Every endpoint resolves the target bank
  through `_acl_read` / `_acl_write` using the authenticated username. Banks are
  isolated SQLite files, and all id lookups run only against the ACL-resolved
  bank, so a memory id cannot cross a bank boundary. Unauthorized access returns
  `404` to hide a bank's existence.
* **Parameterized SQL.** All queries — including tag `IN (...)` clauses — use
  bound parameters; user input is never string-formatted into SQL.
* **Trusted db_path handling.** Bank `db_path`s come from trusted admin config
  and are authoritative (no `db_dir` containment). They are `realpath`-normalized
  and must be unique — two banks may never resolve to the same file (a duplicate
  aborts startup). Bank `id`s are unique and never used to build filesystem
  paths; each `db_path`'s parent directory is auto-created at startup.
* **Input validation & limits.** `name` (≤ 256), `content` (≤ 64 KiB), tag
  format/length, tag count (≤ 32), and page size (≤ 500) are all bounded to
  reject abusive input.
* **No PII in logs.** Errors are caught and returned as generic messages; no
  stack traces, SQL, or memory content reach the client, and memory content is
  never logged (only the bank id and an error string).
* **No hardcoded secrets.** The template config uses placeholders; real
  credentials live only in the git-ignored `cwshugg_membank.yaml`.

## Deferred Work

**Phase 4 — event-source integration is planned but not yet implemented.** The
design anticipates event-producing services (e.g. dedicated service accounts)
writing to a normal "events" bank via `/memory/add`, with humans holding
read-only access, plus an optional retention/pruner worker. In the shipped
service an events bank is just an ordinary bank configured with least-privilege
ACLs (writers add, humans read); no automatic event ingestion or retention
pruning exists yet.

## Dependencies

* **Library modules:** `lib.service`, `lib.oracle`, `lib.config`, `lib.cli`,
  `lib.dialogue`, `lib.nla`, `lib.uniserdes`, `lib.lock`
* **Other services:** [Speaker](speaker.md) (NLA dispatch) and
  [Telegram](telegram.md) (the `/memory` command)
* **External APIs:** OpenAI (used only by the NLA layer for store/query
  extraction)
* **Storage:** SQLite (one database file per bank, WAL journaling)
