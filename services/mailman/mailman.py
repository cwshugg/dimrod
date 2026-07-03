#!/usr/bin/python3
# The mailman service: an EMAIL front-end onto DImROD's `speaker`, exactly
# analogous to how the `telegram` service is a CHAT front-end onto `speaker`.
#
# WHAT IT DOES
# ------------
# mailman watches a dedicated email inbox over a long-lived IMAP IDLE
# connection. When new mail arrives it enqueues the UNSEEN message UIDs onto a
# thread-safe work queue. A configurable pool of worker threads each:
#   1. FETCHes the full message by UID *without* marking it \Seen,
#   2. extracts the bare from-address and applies a HARD, FAIL-CLOSED allowlist,
#   3. DISALLOWED  -> logs one line, calls no speaker, sends no reply, then
#                     permanently deletes the message,
#      ALLOWED     -> combines subject+body into one `message` (from-address is
#                     passed as `author_name`, never concatenated into the body),
#                     resolves the email thread's speaker `conversation_id`,
#                     POSTs `/talk` to speaker (reusing telegram's mechanism),
#                     builds a threaded `Re:` reply, sends it over SMTP, records
#                     the thread<->conversation mapping, then permanently deletes
#                     the message,
#   4. on speaker failure for an allowed sender -> sends a friendly in-thread
#      error reply, then deletes.
#
# THE CORE INVARIANT is DELETE-ONLY-AFTER-FULLY-HANDLED: a message is deleted
# only once its reply (or error reply) is confirmed sent, or after a disallowed
# message has been logged. Any failure that must precede deletion leaves the
# message in place (still UNSEEN) so nothing is ever silently lost.
#
# Conversation continuity across an email thread is provided by a persisted
# `ConversationMap` (see `conversation_map.py`) keyed on RFC 5322 Message-IDs.
#
# SECURITY NOTES
# --------------
#   * The allowlist is the primary control and is fail-closed: an empty/missing
#     allowlist denies EVERY sender.
#   * The account app-password lives only in the git-ignored `cwshugg_*.yaml`
#     and is NEVER logged. Message bodies are never logged either (only the
#     UID / from-address / subject are, for traceability).
#   * Sender authenticity (SPF/DKIM/DMARC) is intentionally out of scope; a
#     clean integration hook (`authenticity_ok`) is left AFTER parse and BEFORE
#     the allowlist accept for a future check to slot into.
#
#   Byteboy (Developer)

# Imports
import os
import sys
import time
import threading

# Enable import from the parent (services/) directory so `lib.*` resolves.
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle, OracleSession, OracleSessionConfig
from lib.cli import ServiceCLI
from lib.email_client import EmailClient, EmailClientConfig, EmailClientError, EmailConnectionError

# Local (service) imports. `conversation_map` sits beside this module; the
# services/ dir is already on sys.path (above) but the service dir may not be
# when run directly, so add it defensively.
_this_dir = os.path.dirname(os.path.realpath(__file__))
if _this_dir not in sys.path:
    sys.path.append(_this_dir)
from conversation_map import (
    ConversationMap,
    ConversationMapConfig,
    parse_reference_ids,
    thread_key_for,
    normalize_message_id,
)


# ================================ Constants ================================= #
# Default number of worker threads in the pool.
MAILMAN_DEFAULT_WORKER_COUNT = 4

# Default per-cycle IDLE wait (seconds). Capped internally by the email client's
# `idle_refresh_interval`, so a value >= that interval simply means "block until
# the next refresh or a new-mail event".
MAILMAN_DEFAULT_IDLE_WAIT = 1740

# Default reconnect backoff bounds (seconds) after an IMAP drop.
MAILMAN_DEFAULT_RECONNECT_DELAY = 10
MAILMAN_DEFAULT_RECONNECT_DELAY_MAX = 300

# Default cap (bytes) on how much body text is relayed to speaker. Oversized
# bodies are truncated (and logged) before the speaker call; the message is
# still processed and deleted like any other.
MAILMAN_DEFAULT_MAX_MESSAGE_BYTES = 1048576

# Default friendly reply sent when speaker cannot be reached / errors for an
# allowed sender.
MAILMAN_DEFAULT_ERROR_REPLY = \
    "Sorry -- I couldn't process your message right now. Please try again later."

# Substring used to recognize speaker's stale/unknown-conversation 400 so we can
# transparently retry the /talk WITHOUT the conversation id (start fresh).
MAILMAN_UNKNOWN_CONVERSATION_HINT = "unknown conversation"


# ============================== Mailman Config ============================= #
class MailmanConfig(ServiceConfig):
    """Configuration for the mailman service.

    Inherits `service_name`, `service_log`, `msghub_name`, and `oracle` from
    `ServiceConfig` and composes the shared `EmailClientConfig` transport block
    plus mailman-specific orchestration fields.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields += [
            # --- Transport (IMAP/SMTP) ---
            # The shared email client config. Holds host/port/TLS, the account
            # username + app-password (SECRET, only in the git-ignored config),
            # mailbox, delete_mode, and the IMAP/SMTP timeouts + idle refresh.
            ConfigField("email",     [EmailClientConfig],   required=True),

            # --- Speaker target (identical shape telegram uses) ---
            ConfigField("speaker",   [OracleSessionConfig], required=True),

            # --- Security allowlist (HARD, FAIL-CLOSED) ---
            # Allowed sender addresses; exact match, case-insensitive. An empty
            # or missing list denies EVERY sender (fail-closed).
            ConfigField("allowlist", [list], required=False, default=None),

            # --- Worker pool / listener tuning ---
            ConfigField("worker_count",          [int], required=False,
                        default=MAILMAN_DEFAULT_WORKER_COUNT),
            ConfigField("idle_wait_timeout",     [int], required=False,
                        default=MAILMAN_DEFAULT_IDLE_WAIT),
            ConfigField("reconnect_delay",       [int], required=False,
                        default=MAILMAN_DEFAULT_RECONNECT_DELAY),
            ConfigField("reconnect_delay_max",   [int], required=False,
                        default=MAILMAN_DEFAULT_RECONNECT_DELAY_MAX),
            ConfigField("max_message_bytes",     [int], required=False,
                        default=MAILMAN_DEFAULT_MAX_MESSAGE_BYTES),

            # --- Conversation continuity ---
            # thread<->conversation_id persistence. Optional block; when omitted
            # a default (enabled) map is built beside the service.
            ConfigField("convo_map", [ConversationMapConfig], required=False,
                        default=None),

            # --- Reply text for the speaker-failure path ---
            ConfigField("error_reply_text",      [str], required=False,
                        default=MAILMAN_DEFAULT_ERROR_REPLY),
        ]


# =============================== Work Queue ================================ #
class MailWorkItem:
    """A single unit of work: one message UID to process. Kept as a tiny class
    (rather than a bare string) so future descriptors can be attached without
    changing the queue contract.
    """
    def __init__(self, uid: str):
        """Constructor."""
        self.uid = str(uid)


class MailWorkQueue:
    """A thread-safe work queue of `MailWorkItem`s (mirrors lumen's
    `LumenThreadQueue`).

    Adds a de-duplication guard on top of the condition-variable queue: a UID
    that is already queued OR currently in-flight is never enqueued again. This
    keeps two overlapping listener scans (or an IDLE event racing a poll) from
    handing the same message to two workers. Workers call `done(uid)` after they
    finish handling an item to release the guard.
    """
    def __init__(self):
        """Constructor."""
        self.lock = threading.Lock()
        self.cond = threading.Condition(lock=self.lock)
        self.queue = []
        # UIDs that are queued or in-flight (guards against double-enqueue).
        self.active = set()

    def push(self, uid: str) -> bool:
        """Enqueues a UID unless it is already queued/in-flight. Returns True if
        the item was enqueued, False if it was a duplicate that was skipped.
        """
        uid = str(uid)
        self.lock.acquire()
        try:
            if uid in self.active:
                return False
            self.active.add(uid)
            self.queue.append(MailWorkItem(uid))
            self.cond.notify()
            return True
        finally:
            self.lock.release()

    def pop(self) -> MailWorkItem:
        """Pops the next item, blocking while the queue is empty. The popped
        UID remains marked in-flight until the caller invokes `done(uid)`.
        """
        self.lock.acquire()
        try:
            while len(self.queue) == 0:
                self.cond.wait()
            return self.queue.pop(0)
        finally:
            self.lock.release()

    def done(self, uid: str) -> None:
        """Releases the in-flight guard for a UID so a future scan may enqueue
        it again (e.g. after a no-loss failure left the message in the mailbox).
        """
        uid = str(uid)
        self.lock.acquire()
        try:
            self.active.discard(uid)
        finally:
            self.lock.release()


# ================================ Worker =================================== #
class MailWorker(threading.Thread):
    """A worker thread that pops UIDs off the queue and processes each one via
    the service. Mirrors lumen's per-item try/except so one bad message never
    kills the pool.
    """
    def __init__(self, service, queue: MailWorkQueue):
        """Constructor."""
        super().__init__(target=self.run)
        self.daemon = True
        self.service = service
        self.queue = queue

    def log(self, msg: str):
        """Writes a log line prefixed with this worker's thread id."""
        ct = threading.current_thread()
        self.service.log.write("[Worker %s] %s" % (ct.native_id, msg))

    def run(self):
        """Thread main loop."""
        self.log("Spawned.")
        while True:
            item = self.queue.pop()
            try:
                self.service.process_item(item)
            except Exception as e:
                # A hard failure while handling one message must not take down
                # the worker. We DID NOT delete the message (delete only happens
                # inside the fully-handled paths), so it survives for a retry.
                self.log("Unhandled error processing uid=%s: %s" % (item.uid, e))
            finally:
                # Release the de-dup guard regardless of outcome.
                self.queue.done(item.uid)


# ================================ Service ================================== #
class MailmanService(Service):
    """The mailman service. Owns the IMAP IDLE listener loop (its own `run()`),
    the worker pool, the two email-client connections (one for the listener's
    IDLE, one shared+locked for worker FETCH/SEND/DELETE), the allowlist, the
    speaker glue, and the conversation map.
    """
    def __init__(self, config_path):
        """Constructor. Parses config and builds state, but opens NO network
        connections and spawns NO threads (that happens in `run()`), so the
        service is cheap to construct in tests.
        """
        super().__init__(config_path)
        self.config = MailmanConfig()
        self.config.parse_file(config_path)

        # Validate the worker count up-front (mirrors lumen's action_threads).
        self.check(self.config.worker_count > 0,
                   "at least one worker (worker_count) must be specified.")

        # Precompute the normalized allowlist set once. Empty/missing => empty
        # set => fail-closed (nobody allowed).
        raw_allowlist = self.config.allowlist or []
        self.allowlist = set(a.strip().lower() for a in raw_allowlist if a)

        # Two independent email clients: one dedicated to the listener's IMAP
        # IDLE loop, and one shared by the workers for FETCH/SEND/DELETE. The
        # email client is not internally thread-safe, so worker access to the
        # shared client is serialized by `worker_client_lock`. The slow speaker
        # call happens OUTSIDE that lock, preserving worker parallelism.
        self.listener_client = EmailClient(self.config.email, log=self.log)
        self.worker_client = EmailClient(self.config.email, log=self.log)
        self.worker_client_lock = threading.Lock()

        # The persisted email-thread <-> conversation_id map. When no block is
        # configured, build a default (enabled) map beside the service.
        cmap_cfg = self.config.convo_map
        if cmap_cfg is None:
            cmap_cfg = ConversationMapConfig()
            cmap_cfg.init_defaults()
        self.convo_map = ConversationMap(cmap_cfg)

        # The work queue and (later-spawned) worker pool.
        self.queue = MailWorkQueue()
        self.workers = []

        # Timestamp of the last conversation-map prune sweep (set in run()).
        self.last_sweep_time = None

        # Monotonic timestamp of the last successful WORKER connect, used to
        # proactively refresh the worker connection before the server drops it
        # (mirrors the listener's IDLE refresh). None until first connected.
        self._worker_connect_time = None

    # ------------------------------- Helpers -------------------------------- #
    def check(self, condition, msg):
        """Custom assertion helper (mirrors lumen's)."""
        if not condition:
            raise Exception("Mailman Error: %s" % msg)

    def spawn_workers(self):
        """Creates and starts the configured number of worker threads. Called
        once from `run()`.
        """
        for _ in range(self.config.worker_count):
            w = MailWorker(self, self.queue)
            w.start()
            self.workers.append(w)

    def notify(self, msg: str):
        """Best-effort ntfy notification (never raises)."""
        try:
            self.msghub.post(msg, title="mailman")
        except Exception as e:
            self.log.write("Failed to post ntfy notification: %s" % e)

    # ------------------------------ Listener -------------------------------- #
    def run(self):
        """Overridden main function: the service thread IS the IMAP IDLE
        listener. Spawns the worker pool, connects the listener client (with
        capped exponential backoff), then loops: SEARCH UNSEEN -> enqueue ->
        IDLE wait, prune-sweeping the conversation map on a timer.
        """
        super().run()

        # Fail-closed visibility: make it obvious when the allowlist is empty.
        if len(self.allowlist) == 0:
            self.log.write("WARNING: allowlist is empty -- ALL mail will be "
                           "ignored and deleted (fail-closed).")

        # Spawn the worker pool and open the shared worker client. Workers block
        # on the queue until the listener enqueues something.
        self.spawn_workers()
        self._connect_with_backoff(self.worker_client, "worker")
        self._connect_with_backoff(self.listener_client, "listener")

        self.last_sweep_time = time.monotonic()
        self.listener_loop()

    def listener_loop(self):
        """The core IMAP IDLE loop. Robust to connection drops (reconnect with
        capped exponential backoff) and to auth failures (notify + back off
        instead of hot-looping).
        """
        backoff = self.config.reconnect_delay
        while True:
            try:
                # Pick up anything already waiting (also catches mail that
                # arrived during an outage, since a reconnect re-runs this).
                self._scan_and_enqueue()

                # Periodically prune the conversation map.
                self._maybe_sweep()

                # Block until a new-mail event or the idle refresh interval.
                self.listener_client.idle_wait(self.config.idle_wait_timeout)

                # A clean cycle resets the backoff.
                backoff = self.config.reconnect_delay
            except EmailClientError as e:
                # Connection/auth trouble. Notify, back off, and try to
                # reconnect. Never tight-loop.
                self.log.write("IMAP listener error: %s" % e)
                self.notify("Mailman IMAP listener error; retrying in %ds." % backoff)
                time.sleep(backoff)
                try:
                    self.listener_client.disconnect()
                except Exception:
                    pass
                try:
                    self.listener_client.connect()
                    backoff = self.config.reconnect_delay
                except EmailClientError as e2:
                    self.log.write("Reconnect failed: %s" % e2)
                    backoff = min(backoff * 2, self.config.reconnect_delay_max)

    def _connect_with_backoff(self, client: EmailClient, label: str):
        """Connects a client, retrying with capped exponential backoff. Used at
        startup so a transient outage doesn't crash the service (and reused to
        re-establish the worker connection after a mid-command drop).
        """
        backoff = self.config.reconnect_delay
        while True:
            try:
                client.connect()
                self.log.write("Connected %s email client." % label)
                if label == "worker":
                    self._worker_connect_time = time.monotonic()
                return
            except EmailClientError as e:
                self.log.write("Failed to connect %s client: %s" % (label, e))
                self.notify("Mailman %s connect failed; retrying in %ds." %
                            (label, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, self.config.reconnect_delay_max)

    def _reconnect_worker(self):
        """Tears down and re-establishes the shared worker IMAP/SMTP connection
        via the capped-backoff connect helper, so a dropped worker socket heals
        instead of getting stuck forever.

        MUST be called with `worker_client_lock` held.
        """
        try:
            self.worker_client.disconnect()
        except Exception:
            # disconnect() is best-effort and already swallows its own errors;
            # guard anyway so a teardown hiccup never blocks the reconnect.
            pass
        self._connect_with_backoff(self.worker_client, "worker")

    def _maybe_refresh_worker(self):
        """Proactively reconnects the worker connection if it has been open
        longer than the configured refresh interval (reusing the transport's
        `idle_refresh_interval`, ~29 min), mirroring the listener's IDLE
        refresh. This makes a mid-command server-side drop rare; the reactive
        reconnect in `_worker_fetch` remains the primary safety net.

        MUST be called with `worker_client_lock` held.
        """
        interval = self.config.email.idle_refresh_interval
        if not interval or interval <= 0:
            return
        if self._worker_connect_time is None:
            # Not yet tracked (e.g. injected in a unit test) -- nothing to do.
            return
        if time.monotonic() - self._worker_connect_time >= interval:
            self.log.write("Proactively refreshing stale worker IMAP connection.")
            self._reconnect_worker()

    def _worker_fetch(self, uid):
        """REFRESHES the worker's mailbox view and FETCHES a message under the
        worker lock, with a self-healing reconnect.

        The worker client is a long-lived connection opened once at startup, so
        (1) its mailbox view is stale for UIDs that arrived after connect -- the
        NOOP refresh flushes pending EXISTS updates so they become fetchable --
        and (2) an idle connection can be silently dropped by the server (Gmail
        closes idle IMAP sockets after ~30 min), which surfaces as an
        `EmailConnectionError` on the next `refresh()`/`fetch()`.

        Recovery: on a CONNECTION-LEVEL drop (`EmailConnectionError`) we
        reconnect the worker ONCE and retry the fetch on the fresh connection,
        all still under the lock. A NON-connection `EmailClientError` (e.g. a
        genuine fetch miss -- the message truly could not be read) is NOT
        retried and is surfaced unchanged, so we never enter a tight reconnect
        loop for a message that simply is not there.

        Returns the fetched `ParsedEmail`. Propagates `EmailClientError` /
        `EmailConnectionError` to the caller, which leaves the message UNSEEN
        for a later retry (no-loss).
        """
        with self.worker_client_lock:
            # Proactive refresh so we rarely hit a server-side idle drop.
            self._maybe_refresh_worker()
            try:
                self.worker_client.refresh()
                return self.worker_client.fetch(uid)
            except EmailConnectionError as e:
                self.log.write("Worker IMAP connection dropped during fetch of "
                               "uid=%s (%s); reconnecting and retrying once." %
                               (uid, e))
            # Reconnect + retry ONCE on the fresh connection (still under the
            # lock). A second failure propagates to the caller.
            self._reconnect_worker()
            self.worker_client.refresh()
            return self.worker_client.fetch(uid)

    def _scan_and_enqueue(self):
        """Searches for UNSEEN messages on the listener connection and enqueues
        each UID exactly once (the queue de-dups queued/in-flight UIDs).
        """
        uids = self.listener_client.search_unseen()
        for uid in uids:
            if self.queue.push(uid):
                self.log.write("Enqueued message uid=%s." % uid)

    def _maybe_sweep(self):
        """Runs the conversation-map prune sweep when the configured interval has
        elapsed. Failures are logged, never fatal.
        """
        if not (self.convo_map.config.enabled and self.convo_map.config.sweep_interval > 0):
            return
        now = time.monotonic()
        if self.last_sweep_time is None:
            self.last_sweep_time = now
            return
        if now - self.last_sweep_time >= self.convo_map.config.sweep_interval:
            self.last_sweep_time = now
            try:
                removed = self.convo_map.sweep()
                if removed:
                    self.log.write("Pruned %d stale conversation-map row(s)." % removed)
            except Exception as e:
                self.log.write("Conversation-map sweep failed: %s" % e)

    # --------------------------- Worker pipeline ---------------------------- #
    def process_item(self, item: MailWorkItem):
        """Handles a single queued message end-to-end. This is the heart of the
        worker pipeline and is written to be directly unit-testable (no threads,
        no network -- inject a fake `worker_client` and a fake speaker session).

        Honors the DELETE-ONLY-AFTER-FULLY-HANDLED invariant throughout.
        """
        uid = item.uid

        # 1. REFRESH then FETCH the full message WITHOUT marking it \Seen.
        #
        #    `_worker_fetch` NOOP-refreshes the worker's mailbox view (so a UID
        #    that arrived AFTER connect becomes visible) and is self-healing: if
        #    the long-lived worker connection was dropped by the server (Gmail
        #    closes idle IMAP sockets after ~30 min), the raw socket/SSL error
        #    is normalized to `EmailConnectionError`, the worker reconnects, and
        #    the fetch is retried ONCE on the fresh connection.
        #
        #    A fetch failure that survives that (a genuine miss, or a drop that
        #    persists across the reconnect+retry) means the message could not be
        #    read right now. We DO NOT delete it: it stays UNSEEN in the inbox,
        #    and the worker loop releases the de-dup guard after we return, so a
        #    later listener scan / reconnect can re-enqueue and retry it. Nothing
        #    is permanently lost.
        try:
            parsed = self._worker_fetch(uid)
        except EmailConnectionError as e:
            self.log.write("FETCH uid=%s failed even after worker reconnect+retry "
                           "(leaving message in inbox for a later retry): %s" %
                           (uid, e))
            return
        except EmailClientError as e:
            self.log.write("FETCH uid=%s failed after IMAP refresh (leaving "
                           "message in inbox for a later retry): %s" % (uid, e))
            return

        from_addr = parsed.from_address
        subject = parsed.subject

        # 2. Authenticity hook. A future SPF/DKIM/DMARC check slots in HERE --
        #    after parse, before the allowlist accept. It is intentionally a
        #    no-op today (always returns True); see `authenticity_ok`.
        if not self.authenticity_ok(parsed):
            self.log.write("Rejecting email from %s (subject: %r): failed "
                           "authenticity check." % (from_addr, subject))
            self._delete(uid, parsed)
            return

        # 3. Allowlist (HARD, FAIL-CLOSED).
        if not self.is_allowed(from_addr):
            # One log line, NO speaker call, NO reply -- then permanent delete.
            self.log.write("Ignoring email from non-allowlisted sender: %s "
                           "(subject: %r)" % (from_addr, subject))
            self._delete(uid, parsed)
            return

        # 4. ALLOWED. Build the combined message and resolve the conversation.
        message = self.compose_message(subject, parsed.body_text)
        candidates = parse_reference_ids(parsed.in_reply_to, parsed.references)
        conversation_id = self.convo_map.lookup(candidates)

        # 5. Talk to speaker (retrying without a stale conversation id if needed).
        response_text, new_cid = self.speak(message, from_addr, conversation_id)

        if response_text is None:
            # Speaker failure for an allowed sender -> friendly in-thread error
            # reply, then delete (only if the error reply actually sent).
            self._handle_speaker_failure(uid, parsed)
            return

        # 6. Build + send the threaded reply. Record the mapping and delete ONLY
        #    after the send is confirmed.
        reply = self.worker_client.build_reply(parsed, response_text)
        reply_msgid = normalize_message_id(reply.get("Message-ID"))
        try:
            with self.worker_client_lock:
                self.worker_client.send(reply)
        except EmailClientError as e:
            # NO-LOSS: send failed -> do NOT delete. The message stays UNSEEN and
            # will be retried on a later scan.
            self.log.write("SMTP send failed for uid=%s (message NOT deleted): %s" %
                           (uid, e))
            self.notify("Mailman failed to send a reply; message retained.")
            return

        # Best-effort: persist the thread<->conversation mapping. A map failure
        # must NOT block deletion of an already-answered email.
        if new_cid:
            try:
                tkey = thread_key_for(parsed.message_id, parsed.references)
                self.convo_map.record_exchange(tkey, parsed.message_id,
                                               reply_msgid, new_cid)
            except Exception as e:
                self.log.write("Failed to record conversation mapping for "
                               "uid=%s: %s" % (uid, e))

        self.log.write("Replied to %s (uid=%s, subject: %r)." %
                       (from_addr, uid, subject))
        self._delete(uid, parsed)

    def _handle_speaker_failure(self, uid: str, parsed):
        """Sends the friendly in-thread error reply, then deletes -- but only if
        the error reply was successfully sent (no-loss preserved).
        """
        reply = self.worker_client.build_reply(parsed, self.config.error_reply_text)
        try:
            with self.worker_client_lock:
                self.worker_client.send(reply)
        except EmailClientError as e:
            self.log.write("Failed to send error reply for uid=%s (message NOT "
                           "deleted): %s" % (uid, e))
            self.notify("Mailman speaker+SMTP both failing; message retained.")
            return
        self.log.write("Sent error reply to %s (uid=%s); speaker was "
                       "unavailable." % (parsed.from_address, uid))
        self._delete(uid, parsed)

    def _delete(self, uid: str, parsed):
        """Permanently deletes a message (mark \\Seen defensively, then delete).
        Deletion failures are logged and surfaced but never crash the worker;
        because the message was already fully handled, a delete failure at worst
        leaves it to be re-handled on a later scan.
        """
        try:
            with self.worker_client_lock:
                # Defensive dedup: flag \Seen before removal so a race can't
                # re-surface it as UNSEEN.
                try:
                    self.worker_client.mark_seen(uid)
                except EmailClientError as e:
                    self.log.write("mark_seen uid=%s failed (continuing to "
                                   "delete): %s" % (uid, e))
                self.worker_client.delete(uid, message_id=parsed.message_id)
            self.log.write("Permanently deleted message uid=%s." % uid)
        except EmailClientError as e:
            self.log.write("Delete failed for uid=%s: %s" % (uid, e))

    # ----------------------------- Building blocks -------------------------- #
    def authenticity_ok(self, parsed) -> bool:
        """Sender-authenticity integration point (SPF/DKIM/DMARC).

        DELIBERATELY a no-op today: always returns True. It exists so a future
        authenticity check has a single, clean place to live -- AFTER the message
        is parsed and BEFORE the allowlist accept -- without touching the rest of
        the pipeline or the conversation map (which sits entirely after this
        boundary). Do NOT implement DKIM/DMARC here without an explicit task.
        """
        return True

    def is_allowed(self, from_address: str) -> bool:
        """Returns True iff `from_address` is on the allowlist. Fail-closed:
        with an empty allowlist this is always False. Comparison is exact and
        case-insensitive (the address is already bare + lowercased by the parser,
        and the allowlist set is pre-lowercased).

        NOTE: `ParsedEmail.from_address` is the FIRST mailbox of the `From:`
        header (via `email.utils.parseaddr`); a multi-address `From:` is thus
        validated on its first address only. This is acceptable here because the
        reply `To:` and `author_name` both use that same first (allowed) address,
        so a second spoofed address receives nothing. Full-header authenticity
        (SPF/DKIM/DMARC) remains out of scope -- see `authenticity_ok`.
        """
        if not from_address:
            return False
        return from_address.strip().lower() in self.allowlist

    def compose_message(self, subject: str, body_text: str) -> str:
        """Combines the decoded subject and (trimmed) plain-text body into the
        single `message` string sent to speaker.

        Canonical shape when both are present:
            Subject: <subject>

            <body>
        If only one is present, that one is sent. The from-address is NOT part of
        this text -- it is passed separately as `author_name`. Oversized bodies
        are truncated to `max_message_bytes` (measured in UTF-8 bytes).
        """
        subj = (subject or "").strip()
        body = (body_text or "").strip()

        # Enforce the body size cap (measured in bytes, truncated on a byte
        # boundary but decoded back to text safely).
        cap = self.config.max_message_bytes
        if cap and cap > 0:
            encoded = body.encode("utf-8", errors="replace")
            if len(encoded) > cap:
                body = encoded[:cap].decode("utf-8", errors="ignore")
                self.log.write("Truncated oversized body to %d bytes." % cap)

        parts = []
        if subj:
            parts.append("Subject: %s" % subj)
        if body:
            parts.append(body)
        if not parts:
            return "(no subject or body)"
        return "\n\n".join(parts)

    def get_speaker_session(self):
        """Creates, logs into, and returns a fresh `OracleSession` with speaker
        (a new session per email -- naturally thread-safe for the pool). Returns
        None if authentication fails.
        """
        s = OracleSession(self.config.speaker)
        try:
            r = s.login()
        except Exception as e:
            self.log.write("Failed to connect to speaker: %s" % e)
            return None
        if not OracleSession.get_response_success(r):
            self.log.write("Failed to authenticate with speaker: %s" %
                           OracleSession.get_response_message(r))
            return None
        return s

    def speak(self, message: str, author_name: str, conversation_id):
        """Calls speaker's `/talk`, reusing telegram's mechanism.

        Returns `(response_text, conversation_id)`:
          * On success -> (the reply text, the conversation id speaker returned
            or None).
          * On a stale/unknown `conversation_id` (speaker 400) -> transparently
            retries WITHOUT the id (start fresh) and returns the fresh result.
          * On any other failure (no session, non-success, exception) ->
            (None, None), signaling the speaker-failure path.

        All per-call state is LOCAL (nothing is stored on the shared service
        instance), so concurrent workers never interfere with one another.
        """
        speaker = self.get_speaker_session()
        if speaker is None:
            return (None, None)

        result, unknown = self._post_talk(speaker, message, author_name,
                                          conversation_id)
        if result is not None:
            return result

        # If we sent a conversation id and speaker rejected it as unknown/stale,
        # retry once WITHOUT it (start a fresh conversation).
        if conversation_id is not None and unknown:
            self.log.write("Conversation id was stale; retrying /talk fresh.")
            result, _ = self._post_talk(speaker, message, author_name, None)
            if result is not None:
                return result

        return (None, None)

    def _post_talk(self, speaker, message, author_name, conversation_id):
        """POSTs a single `/talk` request.

        Returns a `(result, unknown_conversation)` tuple where:
          * `result` is `(response_text, cid)` on success, else `None`; and
          * `unknown_conversation` is True only when the failure was speaker's
            unknown-conversation 400 (so the caller may retry without the id).
        """
        payload = {"message": message}
        if author_name:
            payload["author_name"] = author_name
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id

        try:
            r = speaker.post("/talk", payload=payload)
        except Exception as e:
            self.log.write("Speaker /talk request failed: %s" % e)
            return (None, False)

        # Determine success. A malformed / non-JSON speaker response (e.g. a
        # Flask 500 HTML page or a proxy 502/504) makes get_response_success()
        # -> response.json() raise; treat any such parse error as a speaker
        # failure so it routes to the friendly-error+delete path rather than
        # hot-looping the message forever.
        try:
            ok = OracleSession.get_response_success(r)
        except Exception as e:
            self.log.write("Speaker /talk returned an unparseable response: %s" % e)
            return (None, False)

        if ok:
            try:
                data = OracleSession.get_response_json(r)
            except Exception as e:
                self.log.write("Speaker /talk response JSON was unparseable: %s" % e)
                return (None, False)
            response_text = data.get("response") if isinstance(data, dict) else None
            cid = data.get("conversation_id") if isinstance(data, dict) else None
            if response_text is None:
                self.log.write("Speaker /talk succeeded but returned no response.")
                return (None, False)
            return ((str(response_text), cid), False)

        # Non-success: detect the stale/unknown-conversation case for retry.
        try:
            status = OracleSession.get_response_status(r)
            msg = OracleSession.get_response_message(r)
        except Exception:
            status, msg = None, ""
        unknown = bool(status == 400 and msg and
                       MAILMAN_UNKNOWN_CONVERSATION_HINT in str(msg).lower())
        self.log.write("Speaker /talk returned failure (status=%s): %s" %
                       (status, msg))
        return (None, unknown)


# ================================= Oracle ================================== #
class MailmanOracle(Oracle):
    """Optional HTTP control surface for mailman (enabled with `--oracle`).

    Included for parity with every other DImROD service. Provides a lightweight
    status endpoint on top of the base oracle's `/`, `/id`, and auth routes; the
    core listener/worker pipeline does not require it.
    """
    def endpoints(self):
        """Endpoint definition function."""
        super().endpoints()

        import flask

        @self.server.route("/status")
        def endpoint_status():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            svc = self.service
            status = {
                "service": svc.config.service_name,
                "worker_count": svc.config.worker_count,
                "allowlist_size": len(svc.allowlist),
                "queued": len(svc.queue.queue),
                "convo_map_enabled": svc.convo_map.config.enabled,
            }
            return self.make_response(success=True, payload=status)


# ================================== Main =================================== #
if __name__ == "__main__":
    cli = ServiceCLI(config=MailmanConfig,
                     service=MailmanService,
                     oracle=MailmanOracle)
    cli.run()
