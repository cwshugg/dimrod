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
* Offer both a structured Telegram `/memory` (`/m`) command that maps 1:1 onto
  the oracle endpoints and a conversational natural-language path (plain
  messages) that **stores** and **recalls** notes by inferring intent

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
| `default_bank` | `str` | ✗ | `None` | **Service-level default** memory bank **id** used by the NLA layer when a request neither names a bank nor supplies a per-request default (see [bank resolution](#bank-resolution-precedence)). Existence is validated at **startup** (an unknown id is fatal); per-user accessibility is validated at **request time**. `null`/omitted disables it |
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

# Service-level DEFAULT memory bank id for the NLA layer, used when a request
# neither names a bank nor carries a per-request (telegram per-chat) default.
# Must be one of banks[].id below; existence is validated at startup (fatal if
# unknown), accessibility per-user at request time. Omit/null to disable.
default_bank: personal

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
    "keyword": "section g7",
    "keywords": ["park", "parked", "parking", "car", "vehicle"]
  },
  "limit": 100,
  "offset": 0,
  "order": "desc"
}
```

All `filters` are optional and combined with `AND`. `tag_mode` is `"any"`
(default) or `"all"`; `keyword` is a single substring matched against `name` +
`content`. `keywords` is an **optional list** of substrings: each is matched
against `name` + `content` and the terms are **OR'd together** into one group
(a memory matches if it contains ANY term), then that group is ANDed with the
tag/time clauses. `keyword` and `keywords` may be given together (their terms
are unioned, de-duped); a lone `keyword` behaves exactly as before (the
`/m search` contract is unchanged). The compiled term set is bounded to 12
terms. `limit` defaults to 100 and is hard-capped at 500. `order` is `"desc"`
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
| `recall` | `nla_recall` | Query notes: turns a question into structured filters `{tags, tag_mode, time_range, keyword, keywords, bank?}` (the current UTC time is injected so phrases like "last month" resolve, and coarse day-granular ranges are padded ±12h), retrieves candidates with a **wider net**, then applies an **LLM relevance filter** before rendering matches |

The Speaker's router decides store-vs-query intent by selecting one of these two
endpoints. Membank never guesses: the LLM extraction happens here, and the
resulting explicit values flow into the same "dumb" bank operations the HTTP API
uses.

#### Bank resolution precedence

Both handlers resolve their target bank through one shared resolver
(`MembankOracle.resolve_nla_bank`), which enforces this precedence:

1. **(a) a bank named in the user's message** — resolved via
   `MemoryBankRegistry.resolve_ref`, a natural-language matcher that matches
   (case/whitespace-insensitively) by bank **id**, then human **name**, then a
   **unique substring** of a name. The candidate pool is ACL-filtered up front
   (writable banks for `remember`, readable banks for `recall`), so a match is
   always one the invoking account may use. An ambiguous substring (two or more
   matches) is treated as unresolved.
2. **(b) the per-request default** — `request_data.membank.default_bank`, the
   Telegram per-chat target bank.
3. **(c) the service-level default** — `MembankConfig.default_bank` (see
   [config](#configuration)).
4. **(d) a "which bank?" clarification** — when nothing resolves.

Special rule for **(a)**: if the utterance *named* a bank but it does not
resolve to an accessible bank, the handler returns a clarification listing the
accessible banks rather than silently falling through to (b)/(c) — so a
mistyped or unauthorized bank name never causes a read/write against the wrong
bank. Fallthrough to (b)/(c)/(d) happens only when the utterance named **no**
bank at all. Inaccessible per-request/service defaults are skipped (accessibility
is a per-user, request-time property).

#### Wider-net recall + LLM relevance filter

Conversational recall casts a **wide retrieval net** and then uses the LLM to
keep only the relevant results, so obvious matches are not lost to an
over-specific substring or a hard UTC time bound (e.g. *"Where did I park my car
earlier today"* must recall a stored *"I parked in spot 205"*). The pipeline:

1. **Multi-keyword extraction** — `extract_query_filters` emits a sanitized
   `keywords` list (individual lowercase words plus light inflection/synonym
   variants the model infers, e.g. `park → park, parked, parking`;
   `car → car, vehicle`). The list is stopword-filtered, min-length-2, de-duped,
   and capped at 12. The legacy single `keyword` is retained for back-compat; if
   the model returns only `keyword`, it is tokenized to backfill `keywords`.
   Coarse day-granular time phrases are padded ±12h so a local-time "today" is
   not cut off by a UTC midnight boundary.
2. **OR search** — the `keywords` terms are matched as an **OR-group** over
   `name` + `content` (a memory matches if it contains ANY term), ANDed with any
   tag/time clauses. A lone `keyword` still compiles to the exact legacy clause.
3. **Initial narrowing pass** — the extracted filters (including `time_range`)
   are applied, over-fetching up to 25 candidates (newest-first).
4. **Fallback widened pass** — if the initial pass returns **nothing**, the
   `time_range` is dropped and two cheap SQL reads are unioned in Python: the
   keywords over `name`/`content`, **and** the keywords (plus any extracted
   tags) matched as OR-**tags** (the one place keyword↔tag matching is enabled).
   This step makes **zero** extra LLM calls.
5. **Relevance filter** — the candidates (≤25) plus the original question and
   the current UTC time are sent to **one** bounded LLM call
   (`filter_relevant_candidates` / `nla_filter_relevance`), which returns the
   ids of the relevant memories (id-selection only — stored content is never
   rewritten). Hallucinated/unknown ids are ignored; the kept set is rendered
   verbatim, capped at 10.

Recall therefore adds **at most one** extra LLM call (the relevance filter); the
fallback widening is pure SQL. If the relevance filter **fails** (raises or
returns unparseable output), recall **degrades to the raw candidates** as a HIT
— never a false MISS.


#### Recall outcomes (self-contained HIT / MISS)

A `recall` produces one of three outcomes, all composed **entirely inside the
membank `recall` handler** (the Speaker performs no special completion logic):

- **HIT** — a `recall` where the relevance filter keeps **at least one** memory
  (or a filter failure degrades to the raw candidates) returns `success=True`
  with the RAW, HTML-escaped findings message. The user receives **only** the
  memory findings; no general-answer completion runs (the memories *are* the
  answer).
- **MISS** — a `recall` where nothing is retrieved (even after the widened
  fallback) or the relevance filter keeps **none** returns `success=True`
  with a single **RAW** message composed by the handler itself: a **short "no
  results" notice** (`"I didn't find anything in the memory bank."`) followed by
  a **general-purpose LLM answer** to the user's original question, joined by a
  blank line (`"<notice>\n\n<answer>"`). The completion is run **in the handler**
  via the same membank dialogue used for extraction (`nla_general_answer`). The
  answer text is HTML-escaped so the composed RAW message is safe for the
  downstream Telegram HTML renderer. If the completion **fails**, the error is
  logged and the notice surfaces **on its own** — the NLA never crashes.
- **Hard errors** — extraction/search failures and clarifications return a clear
  `REWORD` error message.

`remember` is always a plain confirmation. The recall miss fallback is local to
membank (composed inside `nla_recall`, not by the speaker).

#### Remember: existing-tag suggestions

To keep tags consistent, `nla_remember` surfaces the target bank's **existing
tags** to the extraction LLM as reuse suggestions so it prefers an existing tag
over inventing a near-duplicate. Before extraction, the handler resolves the
**default** target bank (per-request default → service default, via
`resolve_nla_bank` with **no** named ref, since the utterance-named bank is not
known until *after* extraction — this keeps a **single** extraction call) and
reads its tags through the same in-process `list_tags` path that backs
[`/tag/list`](#post-taglist) and `/m tags`. The tags are injected into the
prompt as `"Existing tags in this bank (PREFER reusing an existing tag when it
fits; only create a new tag if none apply): ..."`, bounded to
`STORE_TAG_SUGGESTION_CAP` (100, most-common first). The section is omitted
when the bank has no tags. ACLs are respected (suggestions only come from a bank
the caller may use), and if no default bank resolves — or the tag lookup fails —
suggestions are simply omitted and the store is **never** blocked. This changes
neither what is stored nor how tags are sanitized server-side.

#### Dynamic descriptions

`MembankOracle.describe_nla_endpoint` appends a compact, per-request,
ACL-filtered **catalog** of accessible banks to the `remember`/`recall`
descriptions served by `/nla/get` (`remember` lists writable banks, `recall`
lists readable ones), giving the Speaker's router live context on which banks
exist. The catalog is bounded (`NLA_DESC_BANK_CAP`, default 12) with a
`(+K more)` suffix so the router prompt stays small.

## Telegram `/memory` Command

The [Telegram](telegram.md) bot's `/memory` command (alias: `/m`) is a
**structured, deterministic** interface to Membank. Each subcommand maps 1:1
onto a Membank oracle endpoint; the command performs **no** natural-language
parsing and never calls the Speaker or an LLM. (Conversational, non-slash
memory — "remember…" / "what did I save about…" — is handled separately by the
[NLA layer](#natural-language-actions-nla) on the plain-message path.)

### Field syntax

After the subcommand keyword, the remainder is split on `.` into fields, with
whitespace trimmed around each `.` **and** around `=` in `key=value` pairs. So
these parse identically:

```text
/m search lore . kw = mithril . limit = 5
/m search lore.kw=mithril.limit=5
```

**Field 0 is always the optional bank.** Leave it empty or use `-` to target the
chat's configured default bank (see
[per-chat bank mapping](#telegram-per-chat-bank-mapping)); otherwise it is
**fuzzy-matched client-side** against the banks the caller can access, mirroring
the server's `MemoryBankRegistry.resolve_ref` (see
[bank resolution precedence](#bank-resolution-precedence)): exact `id`, then
exact `name`, then a **unique substring** — one accessible bank whose name
contains, or is contained by, the reference (bidirectional containment against
**names only**). If the reference matches **more than one** bank, the reply
lists the candidate banks so you can be more specific; if it matches **none**,
you get a "no matching bank" error. Fields 1+ are the subcommand's parameters.
If the bank field is empty/`-` and the chat has no default bank, the command
returns a clear error.

> **Field-delimiter limitation.** Because `.` separates fields and there is no
> escape mechanism, a field value **cannot contain a literal `.`**. For example,
> `/m add` content that includes a period is split into extra fields — the text
> after the first `.` is consumed as `tags` and then `timestamp`, which
> typically surfaces as a confusing `Bad timestamp` error instead of storing the
> intended note. Omit periods from `name`/`content` (and other field values).

### Subcommands

| Subcommand | Endpoint | Fields after bank |
|------------|----------|-------------------|
| `/m banks` | `POST /bank/list` | none (bank field ignored — lists banks you may access) |
| `/m tags [bank]` | `POST /tag/list` | none — lists tags + counts |
| `/m add [bank].name.content[.tags][.timestamp]` | `POST /memory/add` | `name` (req), `content` (req), `tags` (opt, comma-separated), `timestamp` (opt) |
| `/m get [bank].id` | `POST /memory/get` | `id` (req) — renders the full memory |
| `/m search [bank].k=v.k=v…` | `POST /memory/list` | `key=value` filters (see below) |
| `/m edit [bank].id.k=v…` | `POST /memory/update` | `id` (req) then `name=`, `content=`, `tags=` (only provided fields change) |
| `/m del [bank].id` | `POST /memory/delete` | `id` (req) |
| `/m rebuild [bank]` | `POST /bank/rebuild_tags` | none (admin only; a 403/404 is surfaced gracefully) |
| `/m` or `/m help` | — | prints usage/help for every subcommand |

Unknown subcommands show the help; missing required args produce a clear
per-subcommand usage error.

### Search filters (`/m search`)

Each filter is a `key=value` field:

| Key | Meaning | Maps to |
|-----|---------|---------|
| `kw=` | keyword substring (name + content) | `filters.keyword` |
| `tags=a,b` | tag list | `filters.tags` |
| `mode=any\|all` | tag match mode (default `any`) | `filters.tag_mode` |
| `from=` | lower time bound | `filters.time_range.start` |
| `to=` | upper time bound | `filters.time_range.end` |
| `limit=` | page size (clamped to ≤ 500) | `limit` |
| `offset=` | page offset | `offset` |
| `order=asc\|desc` | sort direction (default `desc`) | `order` |

Results are shown as a compact HTML list (id + name + tags + a content
snippet), honoring the endpoint's `count`/`total`; the reply notes when the page
was truncated by `limit`.

### Date/time formats

`add`'s `timestamp` and `search`'s `from=`/`to=` accept:

* epoch **seconds** (e.g. `1704067200`),
* `YYYY-MM-DD` (e.g. `2024-01-01`), or
* `YYYY-MM-DD HH:MM:SS`

Dates are interpreted as **UTC** and converted to epoch seconds before the
request is sent.

### Examples

```text
/m banks
/m tags lore
/m add .Parking.Section G7 near the elevator.parking,car
/m add lore.Mithril.A light, strong silver-white metal.mithril,mines.2024-01-01
/m get lore.abc123
/m search lore.kw=mithril.tags=mines.mode=all.limit=5.order=asc
/m search .from=2024-01-01.to=2024-02-01
/m edit lore.abc123.name=Mithril ore.tags=mithril,mines
/m del lore.abc123
/m rebuild lore
```

### ACL and errors

The command relies entirely on Membank's server-side, per-user ACL (the Telegram
service authenticates as its own Membank account). Write subcommands on a
read-only bank return `403`, unknown banks/memories return `404`, invalid input
(e.g. a malformed tag) returns `400`, and transient overload returns `503`; the
command surfaces each as a friendly, HTML-escaped message.

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
