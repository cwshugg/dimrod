# Speaker — Dialogue and NLA Hub

Speaker is DImROD's central intelligence service. It wraps the `DialogueInterface` to provide LLM-powered conversations and acts as the dispatcher for [Natural Language Actions](../data-types.md#nla-types) (NLA) across the system.

## Purpose

* Expose conversational AI over HTTP so other services can talk to the LLM
* Manage persistent conversation history (create, retrieve, add messages)
* Dispatch NLA requests to other services based on natural language input
* Maintain DImROD's personality mood and reword responses

## Architecture

Speaker runs two threads:

* **`SpeakerService`** — Background worker that periodically rotates DImROD's mood (based on a configurable timeout)
* **`SpeakerOracle`** — HTTP API server with conversation, message, and NLA endpoints

### NLA Dispatch Flow

When a `/talk` request arrives, Speaker can optionally invoke NLA endpoints on other services:

1. Speaker queries each configured NLA service's `/nla/get` endpoint to discover available actions
2. Speaker asks the LLM which NLA endpoint(s) to invoke based on the user's message
3. Worker threads (`SpeakerNLAThread`) call the selected services' `/nla/invoke/<name>` endpoints concurrently
4. Results are collected, combined, and reworded by the LLM before responding

Whether or not an NLA action fires, a `/talk` turn now participates in a conversation. When an NLA action produces a reply, Speaker appends both the user's message and the NLA-generated reply to the conversation and returns the `conversation_id` (continuing an existing conversation when a valid id is supplied, or creating a new one otherwise). This lets clients that thread by conversation — such as the mailman email service — retain continuity even for action turns.

The NLA dispatch system uses a `SpeakerNLAQueue` backed by `SpeakerNLAQueueEntry` objects. Each entry tracks its status via `SpeakerNLAQueueEntryStatus` (`PENDING`, `PROCESSING`, `SUCCESS`, `FAILURE`) and includes a condition variable so callers can block until the entry completes.

The NLA worker pool size is configurable via `nla_threads`.

### NLA result composition (recall miss is self-contained)

When one or more NLA endpoints fire, Speaker composes their result messages via
`nla_compose_message()` — each result's `message` is either reworded (`REWORD`)
or kept verbatim (`RAW`) per its `message_postprocess` — and returns that text
as the reply.

Key properties (`nla_compose_message` + `endpoint_talk`):

* If no result produced any text, Speaker responds with a graceful empty
  success (no payload).
* The persisted NLA conversation holds a single composed assistant reply.
* The normal no-NLA dialogue path is untouched.

## Oracle Endpoints

### Conversation Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/conversation/create` | Create and save a new conversation |
| `POST` | `/conversation/get` | Retrieve a conversation by ID |
| `POST` | `/conversation/get_last_update` | Get the latest message in a conversation |
| `POST` | `/conversation/addmsg` | Append a message to an existing conversation |

### Message Operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/message/get` | Retrieve a message by ID (returns message + conversation ID) |
| `POST` | `/message/search` | Search messages by ID, author, keywords, or Telegram metadata |
| `POST` | `/message/update` | Update a message's Telegram metadata |

### LLM Operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/talk` | Send a message in a conversation; may trigger NLA dispatch |
| `POST` | `/oneshot` | One-off LLM query with no conversation context |
| `POST` | `/reword` | Reword text using the LLM |

### `/talk` Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `message` | Yes | The user's message text |
| `conversation_id` | No | Existing conversation to continue |
| `author_name` | No | Display name for the message author |
| `author_id` | No | ID of an existing author |

The response payload always includes a `conversation_id` (plus `request_message_id`, `request_author_id`, `response_message_id`, and `response_author_id`) alongside the `response` text — for both normal dialogue turns and NLA-action turns — so callers can continue the same conversation on subsequent requests. An explicitly-provided but unknown `conversation_id` yields a `400 "Unknown conversation ID"` (no conversation is silently created).

## Configuration

| Field | Type | Description |
|-------|------|-------------|
| `dialogue` | `DialogueConfig` | OpenAI API key, model, behavior prompt, moods |
| `tick_rate` | `int` | Service tick interval (seconds) |
| `mood_timeout` | `int` | Seconds between mood rotations |
| `nla_services` | `list[NLAService]` | Services to query for NLA endpoints |
| `nla_dialogue_retry_count` | `int` | LLM retries for NLA dispatch decisions |
| `nla_threads` | `int` | Number of concurrent NLA worker threads |

## Dependencies

* **Library modules:** `lib.dialogue`, `lib.nla`, `lib.oracle`, `lib.service`
* **External APIs:** OpenAI (via `DialogueInterface`)
* **Other services:** Any service registered as an NLA service (e.g., Lumen, Notif)

## Inter-Service Role

Speaker is the most connected service in DImROD:

* **Telegram** calls Speaker for conversation management and LLM operations
* **Taskmaster** calls Speaker for LLM-powered categorization and rewording
* **Cron jobs** call Speaker for nudge generation
* Speaker calls **NLA services** (Lumen, Notif) to execute natural language actions
