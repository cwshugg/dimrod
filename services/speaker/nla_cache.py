#!/usr/bin/python3
# This module implements the speaker service's NLA (Natural Language Actions)
# selection cache.
#
# On every incoming chat message, `SpeakerService.nla_process()` asks an LLM
# "which NLA endpoints should I invoke for this message?". That LLM round-trip
# is the slow, expensive, non-deterministic part of the pipeline, yet the same
# phrasings recur frequently (e.g. "turn on the office lights"). This cache
# stores *only the LLM selection decision* (the ordered list of endpoints +
# their invocation parameters), keyed by a sanitized form of the message, so a
# repeated message can skip the LLM entirely and replay the cached decision.
#
# The cache is:
#   - Backed by a single SQLite table (via `lib/db.py`'s `Database`), storing a
#     JSON blob of the selection plus visible `message_key`/`created_at`/
#     `expiration` columns.
#   - Thread-safe via an internal writer-priority `ReadWriteLock` (from
#     `lib/lock.py`) paired with per-operation SQLite connections + WAL, so
#     concurrent Oracle request threads (and the background sweep) never corrupt
#     it. The lock is held only for the brief in-memory/SQLite operations —
#     never across LLM calls, service pings, or NLA invocation.
#   - Absolutely expiring: an entry's expiration is set once at creation
#     (now + `default_expiration`) and never refreshed. Expired entries are
#     removed lazily on access and by a periodic background sweep.
#
# See `.cobots/reports/95bbd2987a1fd4e0.report.md` for the full design.
#
#   Connor Shugg

# Imports
import os
import sys
import re
import copy
import sqlite3
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
from lib.uniserdes import Uniserdes, UniserdesField
from lib.db import Database, DatabaseConfig
from lib.lock import ReadWriteLock


# ================================ Constants ================================= #
# Name of the single SQLite table used to store cache entries.
NLA_CACHE_TABLE_NAME = "nla_cache"

# Name of the secondary index created on the `expiration` column. The index
# makes the periodic sweep (delete-where-expired) and soonest-expiry capacity
# eviction efficient.
NLA_CACHE_EXPIRATION_INDEX_NAME = "idx_nla_cache_expiration"

# Default filename for the cache database, created beside this module when the
# config does not specify an explicit `path`.
NLA_CACHE_DEFAULT_DB_FILENAME = "nla_cache.db"

# Default absolute expiration for a cached entry, in seconds (two weeks).
NLA_CACHE_DEFAULT_EXPIRATION_SECONDS = 1209600

# Default interval between background sweeps, in seconds (one hour).
NLA_CACHE_DEFAULT_SWEEP_INTERVAL_SECONDS = 3600

# The set of fields kept as visible SQLite columns (rather than being folded
# into the encoded JSON blob). These are the fields we need to index, filter on
# (`WHERE`), and order by. The first of these is also the primary key.
NLA_CACHE_VISIBLE_FIELDS = ["message_key", "created_at", "expiration"]

# A compiled regex matching any run of one-or-more whitespace characters. Used
# by `sanitize_message()` to collapse internal whitespace runs.
_WHITESPACE_RUN_RE = re.compile(r"\s+")


# ============================== Sanitization =============================== #
def sanitize_message(message: str) -> str:
    """Compute the cache key for a message.

    The transformation is:
      - lowercase the message
      - strip leading/trailing whitespace
      - collapse any run of 2+ inner whitespace characters into a single space
      - punctuation is KEPT

    This is used ONLY to compute the cache key. The ORIGINAL message is what
    gets sent to the LLM (on a miss) and passed to NLA handlers (unchanged); the
    sanitized form never replaces the message anywhere else.
    """
    s = message.lower()
    s = s.strip()
    s = _WHITESPACE_RUN_RE.sub(" ", s)
    return s


# ============================ NLA Cache Config ============================== #
class NLACacheConfig(Config):
    """Configuration for the speaker service's NLA selection cache.

    All fields are optional so existing speaker configs (which contain no
    `nla_cache` block) keep parsing; when the block is absent the speaker builds
    a default, enabled cache.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            # When false, the cache is a complete no-op: no DB file is created,
            # `get()` always misses, and all mutating methods do nothing.
            ConfigField("enabled",            [bool], required=False, default=True),
            # Filesystem path to the SQLite database. `None` is resolved by
            # `NLACache.__init__` to `<dir of this module>/nla_cache.db`.
            ConfigField("path",               [str],  required=False, default=None),
            # Absolute lifetime of a cache entry, in seconds (default: 2 weeks).
            ConfigField("default_expiration", [int],  required=False,
                        default=NLA_CACHE_DEFAULT_EXPIRATION_SECONDS),
            # How often the background sweep runs, in seconds (default: 1 hour).
            ConfigField("sweep_interval",     [int],  required=False,
                        default=NLA_CACHE_DEFAULT_SWEEP_INTERVAL_SECONDS),
            # Optional maximum number of entries. When set and exceeded, rows
            # with the soonest expiration are evicted first. `None` = uncapped.
            ConfigField("max_entries",        [int],  required=False, default=None),
        ]


# ============================= NLA Cache Entry ============================== #
class NLACacheEntry(Uniserdes):
    """A single cached NLA-selection decision.

    An entry maps a sanitized message (`message_key`) to the ordered sequence of
    NLA endpoints the LLM selected for it, along with absolute creation and
    expiration timestamps.

    `message_key`, `created_at`, and `expiration` are kept as visible SQLite
    columns (see `NLA_CACHE_VISIBLE_FIELDS`) so they can be indexed, filtered,
    and ordered. `nla_sequence` is folded into the encoded JSON blob column.

    Each element of `nla_sequence` is a plain JSON-friendly dict of the shape:

        {
            "endpoint_id": "lumen::/nla/invoke/set_light",
            "invoke_params": {            # == NLAEndpointInvokeParameters.to_json()
                "message": "...",
                "substring": "...",
                "extra_params": { "request_data": { ... } }
            }
        }

    The endpoint objects themselves are never stored — only their IDs and the
    serialized invocation parameters — so the blob is portable and contains no
    live handles.
    """
    def __init__(self):
        """Constructor."""
        super().__init__()
        self.fields = [
            # The sanitized message; doubles as the SQLite primary key.
            UniserdesField("message_key",  [str],      required=True),
            # Ordered list of {endpoint_id, invoke_params} dicts (JSON blob).
            UniserdesField("nla_sequence", [list],     required=True),
            # When the entry was created (absolute, local time).
            UniserdesField("created_at",   [datetime], required=True),
            # When the entry expires (absolute, local time; never refreshed).
            UniserdesField("expiration",   [datetime], required=True),
        ]

    def is_expired(self, now: datetime = None) -> bool:
        """Returns True if this entry is expired as of `now` (defaults to the
        current local time). Expiration is inclusive: an entry whose expiration
        timestamp equals `now` is considered expired.
        """
        now = now or datetime.now()
        return now.timestamp() >= self.expiration.timestamp()


# ================================ NLA Cache ================================= #
class NLACache:
    """A thread-safe, absolutely-expiring SQLite cache of LLM NLA selections.

    Concurrency model (see report §9):
      - One internal writer-priority, non-reentrant `ReadWriteLock` serializes
        logical cache consistency. Reads (`get` fast path, `count`) take the
        read lock; mutations (`put`, `delete`, `sweep`, eviction, and the rare
        expired-row cleanup inside `get`) take the write lock.
      - Every public method opens a fresh, per-operation SQLite connection so
        that concurrent read-lock holders never share a connection object
        (Python's `sqlite3` forbids concurrent use of one connection). WAL mode
        is enabled at table creation to keep the file layer robust.
      - Multi-statement write methods take the write lock once and call
        `_*_locked` helpers (which assume the caller already holds it) to honor
        the lock's non-reentrancy.

    When `config.enabled` is false the cache is a complete no-op: no database
    file is created, no lock is ever taken, `get()` returns `None`, and the
    mutating methods do nothing.
    """
    def __init__(self, config: NLACacheConfig):
        """Constructor. Resolves the database path, creates the table + index,
        and (when enabled) enables WAL mode. Does no filesystem work at all when
        the cache is disabled.
        """
        self.config = config

        # Resolve the database path. A `None` path means "beside this module".
        if self.config.path is None:
            self.config.path = os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                NLA_CACHE_DEFAULT_DB_FILENAME
            )

        # The single readers-writer lock guarding all cache state.
        self.lock = ReadWriteLock()

        # When disabled, perform NO filesystem/DB work whatsoever.
        if not self.config.enabled:
            return

        # Make sure the parent directory for the database exists.
        parent_dir = os.path.dirname(os.path.realpath(self.config.path))
        if len(parent_dir) > 0 and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        # Create the table, index, and enable WAL once at startup.
        self._init_db()

    # --------------------------- DB plumbing ------------------------------- #
    # Per-connection pragmas applied to every operation's connection:
    #   - busy_timeout: wait (ms) for the file lock instead of failing fast with
    #     "database is locked" if another connection is mid-write. Our RwLock is
    #     the primary serializer, but WAL still uses a brief file lock at commit
    #     time; the timeout makes that robust under load.
    #   - synchronous=NORMAL: in WAL mode this is durable across application
    #     crashes (just not OS power loss) and avoids an fsync on every commit,
    #     which is the right trade-off for a rebuildable cache and keeps the
    #     write-heavy paths fast.
    _CONNECTION_BUSY_TIMEOUT_MS = 5000

    def _connect(self):
        """Opens a fresh, per-operation SQLite connection (wrapped in a
        `Database`) and applies the standard per-connection pragmas. Returns the
        `(Database, sqlite3.Connection)` pair; the caller must close the
        `Database` via `db.close_connection()`.

        Per-operation connections are what make the read lock both meaningful
        and safe: two threads holding the read lock get independent connections
        rather than sharing one (which `sqlite3` forbids).
        """
        db_config = DatabaseConfig()
        db_config.path = self.config.path
        db = Database(db_config)
        conn = db.get_connection()
        conn.execute("PRAGMA busy_timeout = %d" % self._CONNECTION_BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA synchronous = NORMAL")
        return db, conn

    def _init_db(self):
        """Creates the cache table + expiration index (if absent) and enables
        WAL journaling. Runs once from the constructor.
        """
        db, conn = self._connect()
        try:
            # WAL mode is a persistent, database-level pragma; setting it once
            # is sufficient. It keeps concurrent reader connections robust and
            # avoids spurious "database is locked" errors at the file layer.
            conn.execute("PRAGMA journal_mode=WAL")

            # Build the CREATE TABLE statement from the entry definition so the
            # schema always matches `NLACacheEntry`'s visible fields. The table
            # generator inspects each visible attribute, so we set them on a
            # throwaway entry first (their values are irrelevant — only the
            # declared field types drive the column types).
            schema_entry = NLACacheEntry()
            for name in NLA_CACHE_VISIBLE_FIELDS:
                setattr(schema_entry, name, None)
            table_def = schema_entry.get_sqlite3_table_definition(
                NLA_CACHE_TABLE_NAME,
                fields_to_keep_visible=NLA_CACHE_VISIBLE_FIELDS,
                primary_key_field="message_key"
            )
            conn.execute(table_def)

            # Secondary index on `expiration` for efficient sweep + eviction.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS %s ON %s(expiration)" %
                (NLA_CACHE_EXPIRATION_INDEX_NAME, NLA_CACHE_TABLE_NAME)
            )
            conn.commit()
        finally:
            db.close_connection()

    # ------------------------------ Reads ---------------------------------- #
    def get(self, message: str) -> "NLACacheEntry":
        """Looks up a cache entry by `message` (sanitized internally).

        Returns a DETACHED `NLACacheEntry` copy on a fresh hit (so the caller can
        validate/replay it without touching the cache again), or `None` on a
        miss. If the stored entry is found but expired, it is conditionally
        deleted (lazy expiry) and `None` is returned.

        Locking: the fast path takes only a READ lock. On the rare expired
        case, the read lock is fully RELEASED before a brief WRITE lock is taken
        to clean up the row — we never upgrade a held read lock to a write lock
        (the lock is non-reentrant; upgrading would deadlock).
        """
        # Disabled cache: never hit, never lock, never touch the DB.
        if not self.config.enabled:
            return None

        key = sanitize_message(message)

        # ---- Fast path: READ lock, single SELECT by primary key. ----
        self.lock.acquire_read()
        try:
            entry = self._lookup_locked(key)
            if entry is None:
                return None
            if not entry.is_expired():
                # Fresh hit. The entry is already a detached object built from
                # the row, so it is safe to hand back directly.
                return entry
            # Otherwise the row is expired; fall through to cleanup below. We
            # remember its `created_at` so the delete is conditional (and so a
            # concurrently-inserted fresh entry is never dropped).
            expired_created_at = entry.created_at
        finally:
            self.lock.release_read()

        # ---- Expired-row cleanup: WRITE lock (no upgrade — read released). ----
        self.lock.acquire_write()
        try:
            # Re-evaluate under the write lock and delete ONLY if still the same
            # expired row. If another thread replaced it with a fresh entry in
            # the gap, this conditional delete is a harmless 0-row no-op.
            self._delete_key_locked(key, created_at=expired_created_at,
                                    require_expired=True)
        finally:
            self.lock.release_write()
        return None

    def count(self) -> int:
        """Returns the number of rows currently in the cache. Takes a READ
        lock. Returns 0 when the cache is disabled.
        """
        if not self.config.enabled:
            return 0

        self.lock.acquire_read()
        try:
            return self._count_locked()
        finally:
            self.lock.release_read()

    # ------------------------------ Writes --------------------------------- #
    def put(self, message: str, nla_sequence: list,
            expiration: datetime = None) -> None:
        """Inserts (or replaces) the cached NLA selection for `message`.

        `created_at` is set to now and `expiration` to `now + default_expiration`
        unless an explicit `expiration` datetime is supplied (used by tests).
        Uses `INSERT OR REPLACE` on the `message_key` primary key, so repeated
        puts for the same key are idempotent with writer-wins semantics. After
        inserting, capacity eviction runs (still under the write lock).

        No-op when the cache is disabled.
        """
        if not self.config.enabled:
            return

        key = sanitize_message(message)

        # Truncate timestamps to whole seconds so the float values stored in the
        # INTEGER columns round-trip exactly (important for keyed conditional
        # deletes that compare `created_at`).
        now = self._now_seconds()
        if expiration is None:
            expiration = datetime.fromtimestamp(
                now.timestamp() + self.config.default_expiration
            )

        # Build the entry. `nla_sequence` is deep-copied so the cache owns a
        # private snapshot independent of the caller's list.
        entry = NLACacheEntry()
        entry.message_key = key
        entry.nla_sequence = copy.deepcopy(nla_sequence)
        entry.created_at = now
        entry.expiration = expiration

        self.lock.acquire_write()
        try:
            db, conn = self._connect()
            try:
                # Parameterized INSERT OR REPLACE (injection-safe; avoids
                # quoting bugs with messages containing single quotes).
                conn.execute(
                    "INSERT OR REPLACE INTO %s VALUES (?, ?, ?, ?)" %
                    NLA_CACHE_TABLE_NAME,
                    entry.to_sqlite3(fields_to_keep_visible=NLA_CACHE_VISIBLE_FIELDS)
                )
                conn.commit()
            finally:
                db.close_connection()

            # Enforce the capacity cap (still holding the write lock).
            self._evict_if_over_capacity_locked()
        finally:
            self.lock.release_write()

    def delete(self, message: str, created_at: datetime = None) -> None:
        """Deletes the cache entry for `message` (sanitized internally).

        When `created_at` is provided, the delete is CONDITIONAL on that exact
        creation timestamp, so a newer entry for the same key (inserted between
        a prior `get()` and this `delete()`) is never dropped. When omitted, the
        entry is deleted unconditionally by key.

        No-op when the cache is disabled.
        """
        if not self.config.enabled:
            return

        key = sanitize_message(message)
        self.delete_key(key, created_at=created_at)

    def delete_key(self, message_key: str, created_at: datetime = None) -> None:
        """Deletes by an already-sanitized key (avoids double-sanitizing).

        Behaves like `delete()` with respect to the optional `created_at`
        conditional. No-op when the cache is disabled.
        """
        if not self.config.enabled:
            return

        self.lock.acquire_write()
        try:
            self._delete_key_locked(message_key, created_at=created_at)
        finally:
            self.lock.release_write()

    def sweep(self) -> int:
        """Deletes all expired rows (where `expiration <= now`), then runs
        capacity eviction. Returns the number of rows removed by the expiry
        pass. Intended for the periodic background sweep. Takes one WRITE lock
        for the whole operation.

        Returns 0 when the cache is disabled.
        """
        if not self.config.enabled:
            return 0

        now_ts = self._now_seconds().timestamp()
        self.lock.acquire_write()
        try:
            db, conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM %s WHERE expiration <= ?" % NLA_CACHE_TABLE_NAME,
                    (now_ts,)
                )
                removed = cur.rowcount
                conn.commit()
            finally:
                db.close_connection()

            # Reclaim space if we are still over capacity after expiry.
            self._evict_if_over_capacity_locked()
            return removed if removed is not None and removed > 0 else 0
        finally:
            self.lock.release_write()

    def evict_if_over_capacity(self) -> int:
        """Public wrapper around capacity eviction. Takes a WRITE lock and
        evicts the soonest-to-expire rows until at most `max_entries` remain.
        Returns the number evicted (0 when uncapped or disabled).
        """
        if not self.config.enabled:
            return 0

        self.lock.acquire_write()
        try:
            return self._evict_if_over_capacity_locked()
        finally:
            self.lock.release_write()

    def close(self) -> None:
        """Releases any cache resources. Per-operation connections are already
        closed by each method, so there is nothing persistent to release; this
        exists for graceful-shutdown / test symmetry.
        """
        # Nothing to do: connections are opened and closed per operation.
        return None

    # --------------------------- Locked helpers ---------------------------- #
    # The helpers below assume the caller ALREADY holds the appropriate lock
    # (read for lookups/counts, write for deletes/eviction). They never acquire
    # a lock themselves — this honors the non-reentrant `ReadWriteLock`.

    def _lookup_locked(self, key: str) -> "NLACacheEntry":
        """Reads a single row by primary key and reconstructs the
        `NLACacheEntry`, or returns `None` if absent. Caller must hold a lock.
        """
        db, conn = self._connect()
        try:
            result = db.search(NLA_CACHE_TABLE_NAME, "message_key = ?",
                               params=(key,))
            row = None
            for r in result:
                row = r
                break
            if row is None:
                return None
            return NLACacheEntry.from_sqlite3(
                row, fields_kept_visible=NLA_CACHE_VISIBLE_FIELDS
            )
        finally:
            db.close_connection()

    def _count_locked(self) -> int:
        """Returns the row count. Caller must hold a lock."""
        db, conn = self._connect()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM %s" % NLA_CACHE_TABLE_NAME)
            row = cur.fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            db.close_connection()

    def _delete_key_locked(self, message_key: str, created_at: datetime = None,
                           require_expired: bool = False) -> None:
        """Deletes a row by key under the caller-held write lock.

        The delete is made conditional to be TOCTOU-safe:
          - when `created_at` is provided, only the row with that exact creation
            timestamp is removed (so a newer same-key entry survives);
          - when `require_expired` is set, only a row that is still expired as
            of now is removed (lazy-expiry cleanup that won't drop a fresh
            replacement).

        NOTE: `created_at` is whole-second truncated (see `_now_seconds`), so a
        `created_at`-only conditional delete (i.e. the stale-endpoint delete,
        which does NOT set `require_expired`) can, within a ~1s window, match a
        fresh same-key entry written in the same second. This is a bounded,
        benign race — see the SAFETY NOTE in `_now_seconds()` for the full
        rationale and why it can never produce an incorrect NLA action.
        """
        cmd = "DELETE FROM %s WHERE message_key = ?" % NLA_CACHE_TABLE_NAME
        params = [message_key]
        if created_at is not None:
            cmd += " AND created_at = ?"
            params.append(created_at.timestamp())
        if require_expired:
            cmd += " AND expiration <= ?"
            params.append(self._now_seconds().timestamp())

        db, conn = self._connect()
        try:
            conn.execute(cmd, tuple(params))
            conn.commit()
        finally:
            db.close_connection()

    def _evict_if_over_capacity_locked(self) -> int:
        """If `max_entries` is set and the row count exceeds it, deletes the
        `(count - max_entries)` rows with the SOONEST expiration first. Returns
        the number evicted. Caller must hold the write lock.
        """
        max_entries = self.config.max_entries
        if max_entries is None:
            return 0

        count = self._count_locked()
        if count <= max_entries:
            return 0

        to_evict = count - max_entries
        db, conn = self._connect()
        try:
            # Delete the soonest-to-expire rows. The primary-key subselect keeps
            # this to a single, index-friendly statement. Ties in `expiration`
            # are broken by `created_at` for deterministic behavior.
            conn.execute(
                "DELETE FROM %s WHERE message_key IN ("
                "SELECT message_key FROM %s ORDER BY expiration ASC, created_at ASC "
                "LIMIT ?)" % (NLA_CACHE_TABLE_NAME, NLA_CACHE_TABLE_NAME),
                (to_evict,)
            )
            conn.commit()
        finally:
            db.close_connection()
        return to_evict

    # ------------------------------ Utility -------------------------------- #
    @staticmethod
    def _now_seconds() -> datetime:
        """Returns the current local time truncated to whole seconds.

        Truncating avoids sub-second float drift when timestamps are written to
        the INTEGER columns and later compared in keyed conditional deletes.

        SAFETY NOTE — bounded same-second stale-delete race:
        Because `created_at` is truncated to whole seconds, two `put()`s for the
        same key that land within the same wall-clock second share an identical
        `created_at`. The stale-endpoint delete in `speaker.nla_process()` keys
        its conditional delete on `created_at` ONLY (no `require_expired`), so in
        that ~1s window it could match and remove a fresh replacement entry
        instead of the stale one it observed. This race is intentionally
        accepted: its blast radius is benign — the worst case is that the NEXT
        caller misses the cache and re-runs the LLM (then re-caches). It can
        never cause an incorrect NLA action, because the live `endpoints{}` and
        the current `request_data` — not the cached row — drive every actual
        invocation. The lazy-expiry delete inside `get()` is NOT exposed to this
        race: it additionally requires `expiration <= now` (see
        `_delete_key_locked`'s `require_expired`), which a fresh entry fails.

        Whole-second truncation is preferred over higher-precision keying here
        because it guarantees the SQLite INTEGER round-trip is exact, which is
        what makes the keyed conditional deletes reliable in the first place.
        Tightening the precision would reintroduce float-drift risk to that
        round-trip, so the bounded race is the lower-risk trade-off.
        """
        return datetime.fromtimestamp(int(datetime.now().timestamp()))
