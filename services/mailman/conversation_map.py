#!/usr/bin/python3
# This module implements the mailman service's email-thread -> speaker
# conversation map: a small, thread-safe SQLite store that bridges an email
# thread (identified by RFC 5322 Message-IDs) to a persistent `speaker`
# dialogue `conversation_id`.
#
# WHY THIS EXISTS
# ---------------
# When a human replies to one of mailman's outgoing reply emails, mailman must
# continue the SAME speaker conversation so context is retained across the whole
# email thread (rather than starting fresh every time). Email threading is
# carried by three headers:
#   * Message-ID   -- unique id of every message,
#   * In-Reply-To  -- the parent message's Message-ID,
#   * References    -- the full chain of ancestor Message-IDs.
# On inbound mail we look up the referenced Message-IDs; a hit yields the
# `conversation_id` to continue. On a successful outbound reply we record the
# inbound and outbound Message-IDs (both pointing at the conversation), so the
# human's next reply -- whose `In-Reply-To` is our reply's Message-ID -- resolves
# directly.
#
# The store deliberately contains NO email bodies, subjects, addresses, secrets,
# or dialogue content -- only opaque Message-IDs and the opaque conversation id
# (non-sensitive routing tokens).
#
# CONCURRENCY MODEL (mirrors `services/speaker/nla_cache.py` exactly)
# ------------------------------------------------------------------
#   * One writer-priority `ReadWriteLock` (`lib/lock.py`) serializes logical
#     consistency: reads take the read lock, writes/sweeps take the write lock.
#   * Every operation opens a fresh, per-operation SQLite connection (Python's
#     `sqlite3` forbids sharing one connection across threads) with WAL mode and
#     a `busy_timeout`, so concurrent mailman workers never corrupt the file.
#   * Pruning is by inactivity: rows whose `last_seen` is older than `ttl`
#     seconds are deleted by a periodic `sweep()` (30 days by default, matching
#     the dialogue library's own prune threshold), so a very old reply naturally
#     starts a fresh conversation.
#
#   Byteboy (Developer)

# Imports
import os
import sys
import time

# Enable import from the parent (services/) directory so `lib.*` resolves the
# same way it does for every other service module.
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.db import Database, DatabaseConfig
from lib.lock import ReadWriteLock


# ================================ Constants ================================= #
# Name of the single SQLite table used to store thread->conversation rows.
CONVO_MAP_TABLE_NAME = "conversation_map"

# Secondary index names (thread-level updates/pruning and sweep efficiency).
CONVO_MAP_THREAD_INDEX_NAME = "idx_conversation_map_thread_key"
CONVO_MAP_LAST_SEEN_INDEX_NAME = "idx_conversation_map_last_seen"

# Default filename for the map database, created beside this module when the
# config does not specify an explicit `path`.
CONVO_MAP_DEFAULT_DB_FILENAME = "mailman_convo.db"

# Default inactivity TTL for a thread mapping, in seconds (30 days). Aligned
# with the dialogue library's own prune threshold so mailman "forgets" a thread
# around the same time speaker prunes the underlying conversation.
CONVO_MAP_DEFAULT_TTL_SECONDS = 2592000

# Default interval between background prune sweeps, in seconds (one hour).
CONVO_MAP_DEFAULT_SWEEP_INTERVAL_SECONDS = 3600

# Direction markers (diagnostics only).
CONVO_MAP_DIRECTION_IN = "in"
CONVO_MAP_DIRECTION_OUT = "out"


# ============================== Normalization ============================== #
def normalize_message_id(value) -> str:
    """Normalizes an RFC 5322 Message-ID for use as a stable key.

    Strips surrounding whitespace and a single pair of angle brackets. Case is
    preserved (Message-IDs are compared case-sensitively per RFC 5322). Returns
    "" for a missing/empty value.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if s.startswith("<") and s.endswith(">") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def parse_reference_ids(in_reply_to: str, references: str) -> list:
    """Builds the ordered list of candidate Message-IDs to look up for an
    inbound message, given its `In-Reply-To` and `References` header values.

    Ordering: `In-Reply-To` (the direct parent, most likely to hold the live
    conversation) first, then the `References` chain from newest to oldest.
    Duplicates and empties are removed while preserving first-seen order.
    """
    candidates = []

    irt = normalize_message_id(in_reply_to)
    if irt:
        candidates.append(irt)

    if references:
        # References is a whitespace-separated chain, oldest -> newest. Search
        # newest first so the most recent conversation for the thread wins.
        refs = [normalize_message_id(r) for r in str(references).split()]
        for r in reversed(refs):
            if r:
                candidates.append(r)

    # De-duplicate, preserving order.
    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def thread_key_for(message_id: str, references: str) -> str:
    """Derives a stable per-thread key: the ROOT Message-ID of the thread.

    The root is the first id in `References` when present, else the message's own
    Message-ID. All rows for one thread share this key so pruning and thread
    `last_seen` bumps operate per-thread.
    """
    if references:
        refs = [normalize_message_id(r) for r in str(references).split()]
        for r in refs:
            if r:
                return r
    return normalize_message_id(message_id)


# ========================= Conversation Map Config ========================= #
class ConversationMapConfig(Config):
    """Configuration for the mailman conversation map.

    All fields are optional so a mailman config with no `convo_map` block still
    parses; mailman then builds a default, enabled map beside the service.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            # When false, the map is a complete no-op: no DB file is created,
            # `lookup()` always misses, and mutating methods do nothing. This
            # makes mailman fall back to stateless one-shot behavior.
            ConfigField("enabled",        [bool], required=False, default=True),
            # Filesystem path to the SQLite database. `None` resolves to
            # `<dir of this module>/mailman_convo.db`.
            ConfigField("path",           [str],  required=False, default=None),
            # Inactivity TTL for a thread mapping, in seconds (default 30 days).
            ConfigField("ttl",            [int],  required=False,
                        default=CONVO_MAP_DEFAULT_TTL_SECONDS),
            # How often the background prune sweep runs, in seconds (default 1h).
            ConfigField("sweep_interval", [int],  required=False,
                        default=CONVO_MAP_DEFAULT_SWEEP_INTERVAL_SECONDS),
        ]


# ============================= Conversation Map ============================ #
class ConversationMap:
    """A thread-safe, inactivity-pruned SQLite map of email-thread Message-IDs
    to `speaker` conversation ids.

    Public API:
      * lookup(candidate_ids)                         -> str | None   (read lock)
      * record(thread_key, message_id, cid, direction)               (write lock)
      * record_exchange(thread_key, in_id, out_id, cid)              (write lock)
      * touch_thread(thread_key)                                     (write lock)
      * sweep()                                       -> int          (write lock)
      * count()                                       -> int          (read lock)

    When `config.enabled` is false the map is a complete no-op.
    """
    _CONNECTION_BUSY_TIMEOUT_MS = 5000

    def __init__(self, config: ConversationMapConfig):
        """Constructor. Resolves the DB path, creates the table + indexes, and
        (when enabled) enables WAL. Does no filesystem work when disabled.
        """
        self.config = config

        # Resolve the database path. A `None` path means "beside this module".
        if self.config.path is None:
            self.config.path = os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                CONVO_MAP_DEFAULT_DB_FILENAME
            )

        # The single readers-writer lock guarding all map state.
        self.lock = ReadWriteLock()

        # When disabled, perform NO filesystem/DB work whatsoever.
        if not self.config.enabled:
            return

        # Make sure the parent directory for the database exists.
        parent_dir = os.path.dirname(os.path.realpath(self.config.path))
        if len(parent_dir) > 0 and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        self._init_db()

    # --------------------------- DB plumbing ------------------------------- #
    def _connect(self):
        """Opens a fresh, per-operation SQLite connection (wrapped in a
        `Database`) and applies the standard per-connection pragmas. Returns the
        `(Database, sqlite3.Connection)` pair; the caller must close the
        `Database` via `db.close_connection()`.
        """
        db_config = DatabaseConfig()
        db_config.path = self.config.path
        db = Database(db_config)
        conn = db.get_connection()
        conn.execute("PRAGMA busy_timeout = %d" % self._CONNECTION_BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA synchronous = NORMAL")
        return db, conn

    def _init_db(self):
        """Creates the map table + indexes (if absent) and enables WAL. Runs
        once from the constructor.
        """
        db, conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS %s ("
                "message_id TEXT PRIMARY KEY, "
                "thread_key TEXT NOT NULL, "
                "conversation_id TEXT NOT NULL, "
                "direction TEXT, "
                "created_at INTEGER, "
                "last_seen INTEGER"
                ")" % CONVO_MAP_TABLE_NAME
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS %s ON %s(thread_key)" %
                (CONVO_MAP_THREAD_INDEX_NAME, CONVO_MAP_TABLE_NAME)
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS %s ON %s(last_seen)" %
                (CONVO_MAP_LAST_SEEN_INDEX_NAME, CONVO_MAP_TABLE_NAME)
            )
            conn.commit()
        finally:
            db.close_connection()

    @staticmethod
    def _now() -> int:
        """Returns the current time as whole epoch seconds."""
        return int(time.time())

    # ------------------------------ Reads ---------------------------------- #
    def lookup(self, candidate_ids) -> str:
        """Returns the `conversation_id` for the first of `candidate_ids` that
        has a mapping (in the given order), or `None` if none match / the map is
        disabled / the list is empty.

        Takes a READ lock. Each candidate is normalized before lookup, so
        callers may pass raw header-derived ids.
        """
        if not self.config.enabled:
            return None
        if not candidate_ids:
            return None

        self.lock.acquire_read()
        try:
            db, conn = self._connect()
            try:
                for raw in candidate_ids:
                    key = normalize_message_id(raw)
                    if not key:
                        continue
                    cur = conn.execute(
                        "SELECT conversation_id FROM %s WHERE message_id = ? "
                        "ORDER BY last_seen DESC LIMIT 1" % CONVO_MAP_TABLE_NAME,
                        (key,)
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return str(row[0])
                return None
            finally:
                db.close_connection()
        finally:
            self.lock.release_read()

    def count(self) -> int:
        """Returns the number of rows currently in the map. Takes a READ lock.
        Returns 0 when disabled.
        """
        if not self.config.enabled:
            return 0
        self.lock.acquire_read()
        try:
            db, conn = self._connect()
            try:
                cur = conn.execute("SELECT COUNT(*) FROM %s" % CONVO_MAP_TABLE_NAME)
                row = cur.fetchone()
                return int(row[0]) if row is not None else 0
            finally:
                db.close_connection()
        finally:
            self.lock.release_read()

    # ------------------------------ Writes --------------------------------- #
    def record(self, thread_key: str, message_id: str, conversation_id: str,
               direction: str = CONVO_MAP_DIRECTION_IN) -> None:
        """Upserts a single `message_id -> conversation_id` row (sharing the
        thread's `thread_key`) and bumps every row of that thread's `last_seen`.

        Uses `INSERT OR REPLACE` (writer-wins on the `message_id` primary key).
        No-op when disabled or when `message_id` normalizes to empty.
        """
        if not self.config.enabled:
            return
        mid = normalize_message_id(message_id)
        if not mid:
            return
        tkey = normalize_message_id(thread_key) or mid
        cid = str(conversation_id)
        now = self._now()

        self.lock.acquire_write()
        try:
            db, conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO %s "
                    "(message_id, thread_key, conversation_id, direction, "
                    "created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)" %
                    CONVO_MAP_TABLE_NAME,
                    (mid, tkey, cid, str(direction), now, now)
                )
                # Bump the whole thread's activity timestamp so a still-active
                # thread is not pruned because only some of its rows were
                # recently touched.
                conn.execute(
                    "UPDATE %s SET last_seen = ? WHERE thread_key = ?" %
                    CONVO_MAP_TABLE_NAME,
                    (now, tkey)
                )
                conn.commit()
            finally:
                db.close_connection()
        finally:
            self.lock.release_write()

    def record_exchange(self, thread_key: str, inbound_message_id: str,
                        reply_message_id: str, conversation_id: str) -> None:
        """Convenience: records both the inbound original Message-ID and the
        outbound reply Message-ID as pointing at `conversation_id` for the given
        thread. This is the normal call after a reply is successfully sent.
        """
        if not self.config.enabled:
            return
        self.record(thread_key, inbound_message_id, conversation_id,
                    CONVO_MAP_DIRECTION_IN)
        self.record(thread_key, reply_message_id, conversation_id,
                    CONVO_MAP_DIRECTION_OUT)

    def touch_thread(self, thread_key: str) -> None:
        """Refreshes `last_seen = now` for every row of a thread. No-op when
        disabled or when there are no rows for the thread.
        """
        if not self.config.enabled:
            return
        tkey = normalize_message_id(thread_key)
        if not tkey:
            return
        now = self._now()
        self.lock.acquire_write()
        try:
            db, conn = self._connect()
            try:
                conn.execute(
                    "UPDATE %s SET last_seen = ? WHERE thread_key = ?" %
                    CONVO_MAP_TABLE_NAME,
                    (now, tkey)
                )
                conn.commit()
            finally:
                db.close_connection()
        finally:
            self.lock.release_write()

    def sweep(self) -> int:
        """Deletes rows whose `last_seen` is older than `ttl` seconds. Returns
        the number of rows removed. Takes one WRITE lock. Returns 0 when
        disabled.
        """
        if not self.config.enabled:
            return 0
        cutoff = self._now() - self.config.ttl
        self.lock.acquire_write()
        try:
            db, conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM %s WHERE last_seen < ?" % CONVO_MAP_TABLE_NAME,
                    (cutoff,)
                )
                removed = cur.rowcount
                conn.commit()
            finally:
                db.close_connection()
            return removed if removed is not None and removed > 0 else 0
        finally:
            self.lock.release_write()

    def close(self) -> None:
        """Releases resources. Per-operation connections are already closed by
        each method, so there is nothing persistent to release; provided for
        graceful-shutdown / test symmetry.
        """
        return None
