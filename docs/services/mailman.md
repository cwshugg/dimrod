# Mailman — Email Listener Interface

Mailman is the **email** front-end onto DImROD's [Speaker](speaker.md), exactly analogous to how [Telegram](telegram.md) is the **chat** front-end. It watches a dedicated email inbox over IMAP IDLE and replies to incoming emails by generating each reply through Speaker's `/talk` endpoint.

## Purpose

* Watch a dedicated, private email account and reply to incoming mail using DImROD's dialogue engine.
* Enforce a hard, fail-closed sender **allowlist** so only trusted people can elicit a reply.
* Maintain **conversation continuity** across an email thread, so replying to one of DImROD's answers continues the same Speaker conversation instead of starting over.
* Permanently delete every processed email once it has been fully handled.

## Architecture

The service runs as a standard DImROD service (`MailmanService extends lib.service.Service`) plus an optional `MailmanOracle` HTTP surface (enabled with `--oracle`). Internally it is a producer/consumer pipeline:

```
Inbox (IMAP IDLE) ──▶ Listener thread ──▶ MailWorkQueue ──▶ MailWorker pool ──▶ Speaker /talk ──▶ SMTP reply ──▶ permanent delete
```

* **IMAP IDLE listener** (`MailmanService.run` / `listener_loop`) — a long-lived IMAP connection issues `IDLE`; on any new-mail event it runs `SEARCH UNSEEN` and enqueues each UID once. It proactively refreshes the IDLE connection (~29 min) and reconnects with capped exponential backoff on a drop. Auth failures are surfaced via ntfy rather than hot-looping.
* **`MailWorkQueue`** — a thread-safe, condition-variable queue (mirrors Lumen's `LumenThreadQueue`) that additionally **de-duplicates** UIDs that are already queued or in-flight, so overlapping scans never hand the same message to two workers.
* **`MailWorker` pool** — a configurable number of worker threads. Each pops a UID and runs the full pipeline (below). One bad message never kills a worker (per-item `try/except`).
* **Two email clients** — one dedicated `EmailClient` for the listener's IDLE loop, and one shared `EmailClient` for worker `FETCH`/`SEND`/`DELETE`, guarded by a single lock. Because [`EmailClient`](../library.md) is not internally thread-safe, worker IMAP/SMTP calls are serialized under that lock — but the slow Speaker HTTP call happens **outside** the lock, preserving real worker parallelism. Both connections are **self-healing**: the listener refreshes its IDLE connection and the worker reconnects+retries on a dropped socket (see the worker pipeline below).
* **`ConversationMap`** — a small, persisted SQLite store bridging email threads to Speaker conversation ids (see [Conversation Tracking](#conversation-tracking)).

### Worker pipeline (per message)

1. **Refresh, then fetch** the full message by UID **without** marking it `\Seen`. The worker client is a long-lived connection opened at startup, so it first calls `EmailClient.refresh()` (an IMAP `NOOP`, under the worker lock) to flush the server's pending `EXISTS` updates — otherwise a message that arrived **after** the worker connected is absent from its stale mailbox view and `UID FETCH` returns `OK` with no data. Fetch itself uses `BODY.PEEK[]`. This refresh+fetch is **self-healing** (`_worker_fetch`): the worker connection also sits idle between messages, and providers such as Gmail close idle IMAP sockets after ~30 min. If the connection was dropped, the next `refresh()`/`fetch()` raises `EmailConnectionError` (a raw socket/SSL/`imaplib` abort normalized by the transport — see [library docs](../library.md)); the worker then **disconnects, reconnects with capped backoff, and retries the fetch once** on the fresh connection, all under the worker lock. It also *proactively* reconnects if the worker connection is older than `email.idle_refresh_interval` (~29 min), mirroring the listener's IDLE refresh, so a mid-command drop is rare. A fetch failure that survives the reconnect+retry (or a **genuine** miss — a plain `EmailClientError`, which is *not* treated as a reconnect trigger) is **not** deleted: the message stays `UNSEEN` in the inbox, the de-dup guard is released, and a later listener scan re-enqueues it for retry (nothing is permanently lost, and there is no infinite re-enqueue loop).
2. **Authenticity hook** — a no-op integration point (`authenticity_ok`) sits *after* parse and *before* the allowlist accept. A future SPF/DKIM/DMARC check slots in here without touching the rest of the pipeline. **DKIM/DMARC is intentionally not implemented.**
3. **Allowlist** (hard, fail-closed) — see [Security](#security-allowlist).
   * **Disallowed** → log one line, **no** Speaker call, **no** reply, then permanently delete.
   * **Allowed** → continue.
4. **Compose** the outgoing `message`: the decoded subject and plain-text body are combined into one string (`Subject: <subject>\n\n<body>`); the from-address is passed **separately** as `author_name`, never concatenated into the body. Oversized bodies are truncated to `max_message_bytes`.
5. **Resolve conversation** — look up the thread's Speaker `conversation_id` (see below).
6. **Talk to Speaker** — open an `OracleSession`, `login()`, `POST /talk` with `{message, author_name, [conversation_id]}` (reusing Telegram's mechanism). A stale/pruned conversation id (Speaker `400 "Unknown conversation ID."`) triggers a transparent retry **without** the id.
7. **Reply** — build a properly-threaded `Re:` reply (`In-Reply-To`/`References`) via `EmailClient.build_reply` and `send` it over SMTP.
8. **Record** the thread↔conversation mapping (best-effort).
9. **Delete** the inbound message permanently.

If Speaker fails for an allowed sender, a **friendly in-thread error reply** is sent, then the message is deleted.

### Delete-only-after-fully-handled invariant

A message is deleted **only** once its reply (or error reply) is confirmed sent, or after a disallowed message has been logged. Any failure that must precede deletion (SMTP send failure, etc.) leaves the message in the mailbox (still `\Unseen`) so nothing is ever silently lost. The message is defensively marked `\Seen` immediately before deletion.

## Security (allowlist)

The allowlist is the **primary security control** and is **fail-closed**:

* Only addresses in the configured `allowlist` receive a reply. Matching is **exact and case-insensitive** (the from-address is reduced to its bare, lowercased form).
* An **empty or missing** allowlist denies **every** sender — all mail is ignored and permanently deleted. Mailman logs a prominent warning at startup when the allowlist is empty.
* Disallowed mail is logged as a single line and deleted; it never triggers a Speaker call or a reply, so mailman cannot become a reply/backscatter amplifier.

**Residual risk:** sender authenticity (SPF/DKIM/DMARC) is out of scope, so the header `From` is spoofable. The mitigations are the dedicated, private, low-traffic inbox and the small allowlist. A future authenticity check has a clean home at the `authenticity_ok` hook. Because Speaker's `/talk` can trigger NLA *actions*, operators who consider a spoofed-but-allowlisted `From` unacceptable should treat DKIM/DMARC verification as a follow-up hardening task.

Secrets discipline: the account **app password** lives only in the git-ignored `cwshugg_mailman.yaml` and is **never logged**; message bodies are never logged either (only UID / from-address / subject are, for traceability).

## Conversation Tracking

Mailman continues the same Speaker conversation across an email thread by bridging RFC 5322 threading headers to Speaker `conversation_id`s, persisted in `services/mailman/mailman_convo.db` (`ConversationMap`).

* **On inbound mail**, mailman collects candidate Message-IDs from `In-Reply-To` (first) and the `References` chain (newest→oldest) and looks each up in the map. The first hit yields the `conversation_id` to continue; if none match (or the message has no threading headers), a fresh conversation is started (no id on the first `/talk`).
* **On a successful reply**, mailman records both the inbound original Message-ID and the outbound reply Message-ID as pointing at the conversation (sharing a per-thread `thread_key` = the thread's root Message-ID). The human's next reply — whose `In-Reply-To` is mailman's reply Message-ID — then resolves directly.
* **Stale ids:** if Speaker has since pruned the conversation, its `400 "Unknown conversation ID."` is treated as "start fresh": mailman retries `/talk` without the id and updates the map to the new conversation.
* **Pruning:** rows inactive for longer than `convo_map.ttl` (default **30 days**, aligned with Speaker's own dialogue prune threshold) are removed by a periodic sweep.

The store contains **no** email bodies, subjects, addresses, or secrets — only opaque Message-IDs and conversation ids. It is built on the same concurrency precedent as Speaker's [`nla_cache.py`](../../services/speaker/nla_cache.py): `lib/db.py` + a writer-priority `ReadWriteLock` (`lib/lock.py`) + per-operation WAL connections, which makes concurrent worker access safe. Near-simultaneous replies on the *same not-yet-recorded* thread may briefly diverge into two conversations (an accepted race that mirrors Telegram); searching the full `References` chain makes this rare and subsequent replies re-converge.

## Configuration

Mailman is configured from a committed template `services/mailman/mailman.yaml` (placeholders only). Copy it to a git-ignored `cwshugg_mailman.yaml` and fill in the real secrets there.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `service_name`, `service_log`, `msghub_name`, `oracle` | — | — | Standard `ServiceConfig` fields. |
| `email` | `EmailClientConfig` | — | IMAP/SMTP transport block (host/port/TLS, `username`, `password` **(secret)**, `mailbox`, `delete_mode`, `gmail_trash_folder`, `imap_timeout`, `smtp_timeout`, `idle_refresh_interval`). |
| `speaker` | `OracleSessionConfig` | — | How to reach Speaker (same block Telegram uses). |
| `allowlist` | `list[str]` | `[]` | Allowed senders (fail-closed when empty). |
| `worker_count` | `int` | `4` | Worker-thread pool size (≥ 1). |
| `idle_wait_timeout` | `int` (s) | `1740` | Per-cycle IMAP IDLE wait. |
| `reconnect_delay` / `reconnect_delay_max` | `int` (s) | `10` / `300` | IMAP reconnect backoff bounds. |
| `max_message_bytes` | `int` | `1048576` | Cap on body text relayed to Speaker. |
| `convo_map` | `ConversationMapConfig` | (built by default) | `enabled`, `path`, `ttl` (30 days), `sweep_interval` (1 h). |
| `error_reply_text` | `str` | friendly default | Body used on the Speaker-failure path. |

### Delete modes

* `gmail_trash_expunge` (default) — copy to `[Gmail]/Trash` then expunge Trash. This is the reliable way to **truly delete** on Gmail (a plain `\Deleted`+`EXPUNGE` often only archives).
* `expunge` — RFC-standard `\Deleted`+`EXPUNGE`, correct for non-Gmail/standards-compliant servers.

## Dependencies

Mailman has **no** service-specific third-party dependencies. Its IMAP/SMTP transport is the shared, **stdlib-only** [`services/lib/email_client.py`](../library.md) wrapper (`imaplib` + `smtplib` + `email`); everything else comes from the existing `services/lib/` stack. `services/mailman/requirements.txt` is intentionally empty.

## Deployment

1. Create a **dedicated** email account (e.g. a Gmail account), enable 2FA, and mint an **app password**.
2. Copy `services/mailman/mailman.yaml` → `services/mailman/cwshugg_mailman.yaml` (git-ignored) and fill in the real `msghub_name`, `oracle.auth_secret`, `email.username`/`email.password`, `speaker` credentials, and the real `allowlist`.
3. Copy `services/mailman/mailman.service` → a real unit with absolute paths (git-ignored) and enable it:

   ```ini
   ExecStart=/path/to/scripts/run-service.sh \
       /path/to/services/mailman/mailman.py \
       --config /path/to/services/mailman/cwshugg_mailman.yaml --oracle
   ```

   `scripts/run-service.sh` builds the service `.venv` from `services/lib/requirements.txt` + `services/mailman/requirements.txt` and runs in-venv — no extra steps needed.

The `--oracle` flag is optional (it adds a `/status` health endpoint for parity with other services); the core listener/worker path does not require it.

## Testing

`services/mailman/test_mailman.py` is a fully-mocked `unittest` suite (run `python3 -m unittest test_mailman` from `services/mailman/`). It replaces the three external seams — the `EmailClient`, the Speaker `OracleSession`, and the SQLite map — with in-memory fakes and a temp DB, with **no live network and no real sleeps**. It covers allowlist allow/deny/fail-closed/case-insensitive, UNSEEN de-dup, subject+body→message + `author_name`, threaded reply headers, permanent delete on both paths, delete-only-after-send, the Speaker-failure error reply, conversation reuse vs. a new thread, the stale-id start-fresh fallback, the **worker-refresh-before-fetch** behavior (the worker refreshes its IMAP view before fetching, a message that becomes fetchable only after refresh is processed normally, and a persistent fetch miss neither deletes the message nor leaks the de-dup guard), and the **self-healing worker reconnect** (a connection-level drop on the first refresh/fetch triggers exactly one disconnect→reconnect→retry after which the message is processed; a drop that persists across the retry does not delete and releases the guard; and a genuine "not found" miss never triggers a reconnect).
