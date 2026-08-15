# Data models and per-bank persistence for the membank service.
#
# This module defines:
#   * `Memory`             — a `Uniserdes` subclass persisted (via uniserdes'
#                            SQLite3 conversion) with ALL fields visible and no
#                            `encoded_obj` blob column.
#   * tag sanitization     — trim -> lowercase -> validate `^[A-Za-z_][...]*$`,
#                            plus the input limits enforced on every mutation.
#   * `MemoryBank`         — one configured SQLite database file with a unique
#                            id, a human name, an ACL, and its own in-process
#                            `ReadWriteLock`. All DB business logic (schema init,
#                            CRUD, tag-table sync, filtering, rebuild) lives here
#                            and is guarded by that lock.
#   * `MemoryBankRegistry` — builds and owns all banks from config, enforcing
#                            id/db_path uniqueness.
#
# Concurrency contract (see the architecture report, §6): every public
# `MemoryBank` method acquires the bank's `ReadWriteLock` exactly once at the
# top (read lock for reads, write lock for mutations) and never re-acquires it.
# Each request touches exactly one bank, so at most one lock is ever held and
# there is no lock ordering to violate — hence no deadlock.
#
#   Connor Shugg

# Imports
import os
import re
import sys
import json
import uuid
import sqlite3
from datetime import datetime, timezone

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.uniserdes import Uniserdes, UniserdesField
from lib.lock import ReadWriteLock
from lib.config import Config, ConfigField


# =============================== Input Limits =============================== #
# Bounds on user-supplied input (architecture report §9.6 / Q-4). These keep DB
# growth and abusive payloads in check; they are generous for real notes.
NAME_MAX_LEN = 256              # max characters in a memory `name`
CONTENT_MAX_LEN = 64 * 1024     # max bytes/characters in a memory `content`
TAGS_MAX_COUNT = 32             # max distinct tags per memory
TAG_MAX_LEN = 64                # max characters in a single (sanitized) tag
LIST_LIMIT_DEFAULT = 100        # default page size for /memory/list
LIST_LIMIT_CAP = 500            # hard cap for the page size
KEYWORD_SQL_CAP = 12            # max keyword terms compiled into one OR-group

# The client-facing bank id must be path-safe (never used to build a filesystem
# path, but validated defensively regardless — see §9.2).
BANK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Tag syntax (§3.3): a tag must begin with a letter or underscore and may
# contain letters, numbers, dashes and underscores.
TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# The visible (queryable) columns of the `memories` table, in schema order.
# `Memory` keeps ALL of its fields visible so that a round-trip with
# `include_encoded_obj=False` is lossless (see the report §3.1/§3.4).
MEMORY_VISIBLE_FIELDS = ["id", "timestamp", "name", "content", "tags"]


# ================================ Exceptions ================================ #
class MembankInputError(Exception):
    """Raised for bad/invalid client input (maps to an HTTP 400).

    Carries a human-readable, content-free message suitable for returning to the
    caller.
    """
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MembankConfigError(Exception):
    """Raised for a fatal configuration error (duplicate bank id/path, unsafe
    path, invalid bank id). The service must refuse to start when this is
    raised.
    """
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MembankLockTimeout(Exception):
    """Raised when a bank's `ReadWriteLock` could not be acquired within the
    configured timeout. The oracle maps this to a fail-secure HTTP 503 ("service
    busy") — never a hang, crash, or open access. Because it is raised BEFORE the
    critical section runs, no DB work is performed and no partial write occurs.
    """
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================ Tag Sanitization ============================= #
def sanitize_tag(tag) -> str:
    """Sanitizes a single tag per the pipeline in §3.3: trim surrounding
    whitespace, force lowercase, then validate against ``TAG_RE``.

    Returns the sanitized tag string. Raises `MembankInputError` if the input is
    not a string, exceeds `TAG_MAX_LEN` after trimming, or fails validation.
    """
    if not isinstance(tag, str):
        raise MembankInputError("Each tag must be a string.")

    # 1. trim, 2. lowercase
    sanitized = tag.strip().lower()

    # length guard (after trim, before/around regex — the regex itself is
    # unbounded, so we bound length explicitly)
    if len(sanitized) == 0:
        raise MembankInputError("Tags must not be empty.")
    if len(sanitized) > TAG_MAX_LEN:
        raise MembankInputError(
            "Tag exceeds the maximum length of %d characters." % TAG_MAX_LEN)

    # 3. validate
    if TAG_RE.match(sanitized) is None:
        raise MembankInputError(
            "Invalid tag \"%s\": tags must start with a letter or underscore "
            "and contain only letters, numbers, dashes and underscores." %
            sanitized)

    return sanitized


def sanitize_tags(tags) -> list:
    """Sanitizes a list of tags: applies `sanitize_tag` to each, de-duplicates
    (a memory carries a tag at most once) while preserving first-seen order, and
    enforces the per-memory tag-count limit.

    Returns a list of sanitized, de-duplicated tag strings. Raises
    `MembankInputError` on any invalid tag or if the count limit is exceeded.
    """
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise MembankInputError("\"tags\" must be a list of strings.")

    result = []
    seen = set()
    for tag in tags:
        sanitized = sanitize_tag(tag)
        if sanitized in seen:
            continue
        seen.add(sanitized)
        result.append(sanitized)

    if len(result) > TAGS_MAX_COUNT:
        raise MembankInputError(
            "Too many tags: a memory may carry at most %d tags." %
            TAGS_MAX_COUNT)

    return result


def validate_name(name) -> str:
    """Validates and returns a memory `name` (non-empty, <= NAME_MAX_LEN)."""
    if not isinstance(name, str):
        raise MembankInputError("\"name\" must be a string.")
    stripped = name.strip()
    if len(stripped) == 0:
        raise MembankInputError("\"name\" must not be empty.")
    if len(name) > NAME_MAX_LEN:
        raise MembankInputError(
            "\"name\" exceeds the maximum length of %d characters." %
            NAME_MAX_LEN)
    return name


def validate_content(content) -> str:
    """Validates and returns a memory `content` (non-empty, <= CONTENT_MAX_LEN)."""
    if not isinstance(content, str):
        raise MembankInputError("\"content\" must be a string.")
    if len(content.strip()) == 0:
        raise MembankInputError("\"content\" must not be empty.")
    if len(content) > CONTENT_MAX_LEN:
        raise MembankInputError(
            "\"content\" exceeds the maximum length of %d bytes." %
            CONTENT_MAX_LEN)
    return content


def _like_escape(substr: str) -> str:
    """Escapes a substring for safe use in a ``LIKE ? ESCAPE '\\'`` clause so
    that user-supplied ``%`` and ``_`` are treated literally (exact-substring
    semantics). The backslash escape character itself is escaped first.
    """
    return (substr.replace("\\", "\\\\")
                  .replace("%", "\\%")
                  .replace("_", "\\_"))


# ================================= Memory ================================== #
class Memory(Uniserdes):
    """A single memory (note). Persisted as a `Uniserdes` subclass with all
    fields visible and no `encoded_obj` blob column (`include_encoded_obj=False`).

    The `tags` field is stored on the row as a canonical JSON array string (a
    primitive SQLite-friendly type); the normative tag *index* lives in the
    per-bank `tags` / `memory_tags` tables (see `MemoryBank`).
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            UniserdesField("id",        [str], required=True),
            UniserdesField("timestamp", [int], required=True),
            UniserdesField("name",      [str], required=True),
            UniserdesField("content",   [str], required=True),
            UniserdesField("tags",      [str], required=True),
        ]
        # Explicit defaults so the attributes exist (all fields are required, so
        # `init_defaults` would not set them). These make schema generation and
        # tuple conversion well-defined even for a freshly-constructed object.
        self.id = None
        self.timestamp = 0
        self.name = ""
        self.content = ""
        self.tags = "[]"

    # -------------------------- tag list helpers --------------------------- #
    def get_tag_list(self) -> list:
        """Returns the memory's tags as a Python list (decoded from the JSON
        column). Returns an empty list if the column is malformed.
        """
        try:
            parsed = json.loads(self.tags)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def set_tag_list(self, tags: list):
        """Stores the given list of (already-sanitized) tags as a canonical JSON
        array on the `tags` column.
        """
        self.tags = json.dumps(list(tags))

    def to_api_dict(self) -> dict:
        """Returns the client-facing representation of this memory, with `tags`
        expanded to a list.
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "name": self.name,
            "content": self.content,
            "tags": self.get_tag_list(),
        }

    @classmethod
    def create(cls, name: str, content: str, tags: list, timestamp: int,
               memory_id: str = None):
        """Factory that builds a `Memory` from already-validated/sanitized
        values. Generates a fresh UUID4-hex id when none is supplied.
        """
        mem = cls()
        mem.id = memory_id if memory_id is not None else uuid.uuid4().hex
        mem.timestamp = int(timestamp)
        mem.name = name
        mem.content = content
        mem.set_tag_list(tags)
        return mem


# ============================ MemoryBankConfig ============================= #
class MemoryBankConfig(Config):
    """Configuration for a single memory bank: a unique id, a human name, a
    SQLite file path, the read/write ACL user lists, and an (optional) effective
    lock timeout.

    `lock_timeout` is the bounded wait (seconds) for acquiring this bank's
    per-bank `ReadWriteLock`. It is optional at the per-bank level: when unset
    (`None`), `MemoryBankRegistry.build` populates it from the service-level
    default (`MembankConfig.lock_timeout`). A value of `None`/0 means an
    unbounded wait (the historical behavior).
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("id",           [str],  required=True),
            ConfigField("name",         [str],  required=True),
            ConfigField("db_path",      [str],  required=True),
            ConfigField("read_users",   [list], required=True),
            ConfigField("write_users",  [list], required=True),
            ConfigField("lock_timeout", [float, int], required=False,
                        default=None),
        ]


# =============================== MemoryBank ================================ #
class MemoryBank:
    """A single memory bank: one SQLite database file with a unique id, a human
    name, a read/write ACL, and its own in-process `ReadWriteLock`.

    A `MemoryBank` is fully described by its `MemoryBankConfig` (id, name,
    db_path, read/write ACL user lists, and effective lock timeout). All DB
    access opens a short-lived connection nested inside the lock's critical
    section; no connection is cached or shared across threads.
    """
    def __init__(self, config: "MemoryBankConfig"):
        # The bank is config-driven: every per-bank attribute is referenced
        # through `self.config` (id/name/db_path/read_users/write_users and the
        # effective `lock_timeout`) so there is a single source of truth.
        self.config = config
        self.lock = ReadWriteLock()

    # -------------------------- lock acquisition --------------------------- #
    def _acquire_read(self):
        """Acquires this bank's read lock, honoring `config.lock_timeout`. Raises
        `MembankLockTimeout` (mapped to 503) if the timeout elapses; in that case
        no lock is held and the caller must NOT release it.
        """
        if not self.lock.acquire_read(timeout=self.config.lock_timeout):
            raise MembankLockTimeout(
                "bank \"%s\" is busy; try again shortly." % self.config.id)

    def _acquire_write(self):
        """Acquires this bank's write lock, honoring `config.lock_timeout`. Raises
        `MembankLockTimeout` (mapped to 503) if the timeout elapses; in that case
        no lock is held, no DB work runs, and no partial write occurs.
        """
        if not self.lock.acquire_write(timeout=self.config.lock_timeout):
            raise MembankLockTimeout(
                "bank \"%s\" is busy; try again shortly." % self.config.id)

    # ------------------------------- ACL ----------------------------------- #
    def can_read(self, username: str) -> bool:
        """Returns True if the given user may read/list/query this bank."""
        return username in self.config.read_users

    def can_write(self, username: str) -> bool:
        """Returns True if the given user may create/update/delete in this bank."""
        return username in self.config.write_users

    # --------------------------- connections ------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        """Opens a fresh SQLite connection to this bank's DB file with WAL
        journaling enabled. Callers MUST already hold this bank's lock and MUST
        close the connection when done.
        """
        conn = sqlite3.connect(self.config.db_path)
        # WAL improves read/write concurrency within the process (§Q-8).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # --------------------------- schema init ------------------------------- #
    def init_schema(self):
        """Creates the `memories`, `tags` and `memory_tags` tables (and indexes)
        if they do not exist, under the bank's WRITE lock. If the tag tables are
        empty but memories exist (e.g. a freshly-migrated DB), the tag index is
        rebuilt from the memory rows.

        Startup path: acquires the write lock UNBOUNDED (ignores `lock_timeout`)
        since schema init runs once at startup before requests are served and
        must not fail-secure to 503.
        """
        self.lock.acquire_write()
        try:
            conn = self._connect()
            try:
                self._create_tables_locked(conn)
                conn.commit()
                # self-heal: rebuild the tag index if it is empty but there are
                # memories to derive it from.
                cur = conn.execute("SELECT COUNT(*) FROM memories")
                mem_count = cur.fetchone()[0]
                cur = conn.execute("SELECT COUNT(*) FROM tags")
                tag_count = cur.fetchone()[0]
                if mem_count > 0 and tag_count == 0:
                    self._rebuild_tags_locked(conn)
                    conn.commit()
            finally:
                conn.close()
        finally:
            self.lock.release_write()

    def _create_tables_locked(self, conn: sqlite3.Connection):
        """Creates all tables/indexes. Assumes the write lock is held."""
        # The `memories` table is generated by uniserdes from a template Memory
        # with all fields visible and NO encoded_obj column, so the schema is
        # exactly the queryable columns.
        template = Memory()
        memories_ddl = template.get_sqlite3_table_definition(
            "memories",
            fields_to_keep_visible=MEMORY_VISIBLE_FIELDS,
            primary_key_field="id",
            include_encoded_obj=False,
        )
        conn.execute(memories_ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_ts "
                     "ON memories(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_name "
                     "ON memories(name)")

        # Derived, refcounted tag index tables.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tags ("
            "  tag      TEXT PRIMARY KEY,"
            "  refcount INTEGER NOT NULL"
            ")")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_tags ("
            "  memory_id TEXT NOT NULL,"
            "  tag       TEXT NOT NULL,"
            "  PRIMARY KEY (memory_id, tag)"
            ")")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_tags_tag "
                     "ON memory_tags(tag)")

    # ------------------------------ reads ---------------------------------- #
    def count_memories(self) -> int:
        """Returns the number of memories in this bank (read lock)."""
        self._acquire_read()
        try:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT COUNT(*) FROM memories")
                return cur.fetchone()[0]
            finally:
                conn.close()
        finally:
            self.lock.release_read()

    def get_memory(self, memory_id: str):
        """Returns the `Memory` with the given id, or None (read lock)."""
        if not isinstance(memory_id, str) or len(memory_id) == 0:
            raise MembankInputError("\"id\" must be a non-empty string.")
        self._acquire_read()
        try:
            conn = self._connect()
            try:
                return self._get_memory_locked(conn, memory_id)
            finally:
                conn.close()
        finally:
            self.lock.release_read()

    def _get_memory_locked(self, conn: sqlite3.Connection, memory_id: str):
        """Fetches a single memory by id. Assumes a lock is held."""
        cur = conn.execute(
            "SELECT id, timestamp, name, content, tags "
            "FROM memories WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return Memory.from_sqlite3(row,
                                   fields_kept_visible=MEMORY_VISIBLE_FIELDS,
                                   include_encoded_obj=False)

    def list_memories(self, filters: dict = None, limit: int = None,
                      offset: int = 0, order: str = "desc"):
        """Returns ``(memories, total)`` — a page of memories matching the given
        filters plus the total count of matches (ignoring pagination).

        Filters (all optional, combined with AND):
          * ``time_range``: ``{"start": <epoch>, "end": <epoch>}``
          * ``tags``: list of tags (sanitized before matching)
          * ``tag_mode``: ``"any"`` (default) or ``"all"``
          * ``keyword``: substring matched against name+content (LIKE)
          * ``keywords``: optional list of substrings; each is matched against
            name+content (LIKE) and the terms are OR'd together into one group
            (match ANY term). Unioned with ``keyword`` when both are given.
            Bounded to ``KEYWORD_SQL_CAP`` compiled terms.

        Read lock. Newest-first by default.
        """
        where_sql, params = self._build_filter_clause(filters)

        # normalize pagination inputs
        limit = self._normalize_limit(limit)
        offset = self._normalize_offset(offset)
        order_sql = "ASC" if str(order).lower() == "asc" else "DESC"

        self._acquire_read()
        try:
            conn = self._connect()
            try:
                # total matches (no pagination)
                total_sql = "SELECT COUNT(*) FROM memories"
                if where_sql:
                    total_sql += " WHERE " + where_sql
                total = conn.execute(total_sql, params).fetchone()[0]

                # page of matches, newest-first (ties broken by id for stability)
                page_sql = ("SELECT id, timestamp, name, content, tags "
                            "FROM memories")
                if where_sql:
                    page_sql += " WHERE " + where_sql
                page_sql += (" ORDER BY timestamp %s, id %s LIMIT ? OFFSET ?"
                             % (order_sql, order_sql))
                rows = conn.execute(page_sql,
                                    tuple(params) + (limit, offset)).fetchall()

                memories = [
                    Memory.from_sqlite3(
                        row, fields_kept_visible=MEMORY_VISIBLE_FIELDS,
                        include_encoded_obj=False)
                    for row in rows
                ]
                return memories, total
            finally:
                conn.close()
        finally:
            self.lock.release_read()

    def list_tags(self) -> list:
        """Returns the bank's tags as ``[{"tag": str, "count": int}, ...]``
        sorted by count desc then tag name (read lock).
        """
        self._acquire_read()
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT tag, refcount FROM tags "
                    "ORDER BY refcount DESC, tag ASC")
                return [{"tag": row[0], "count": row[1]} for row in cur.fetchall()]
            finally:
                conn.close()
        finally:
            self.lock.release_read()

    # ------------------------------ writes --------------------------------- #
    def add_memory(self, name: str, content: str, tags: list,
                   timestamp: int = None) -> Memory:
        """Validates + sanitizes inputs, inserts a new memory, and syncs the tag
        tables — all inside a single transaction under the WRITE lock. Returns
        the created `Memory`.
        """
        name = validate_name(name)
        content = validate_content(content)
        tags = sanitize_tags(tags)
        if timestamp is None:
            timestamp = self._now()
        else:
            timestamp = self._validate_timestamp(timestamp)

        mem = Memory.create(name=name, content=content, tags=tags,
                            timestamp=timestamp)

        self._acquire_write()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                tup = mem.to_sqlite3(
                    fields_to_keep_visible=MEMORY_VISIBLE_FIELDS,
                    include_encoded_obj=False)
                conn.execute(
                    "INSERT INTO memories (id, timestamp, name, content, tags) "
                    "VALUES (?, ?, ?, ?, ?)", tup)
                self._tags_add_locked(conn, mem.id, tags)
                conn.commit()
                return mem
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            self.lock.release_write()

    def update_memory(self, memory_id: str, name=None, content=None,
                      tags=None, timestamp=None) -> bool:
        """Updates the provided subset of fields on an existing memory and syncs
        the tag tables inside a single transaction under the WRITE lock. Returns
        True on success, False if the memory does not exist.

        Only provided (non-None) fields change. When ``tags`` is provided it is
        sanitized and REPLACES the memory's tag set.
        """
        if not isinstance(memory_id, str) or len(memory_id) == 0:
            raise MembankInputError("\"id\" must be a non-empty string.")

        # validate/sanitize the provided fields up front (before locking)
        if name is not None:
            name = validate_name(name)
        if content is not None:
            content = validate_content(content)
        new_tags = None
        if tags is not None:
            new_tags = sanitize_tags(tags)
        if timestamp is not None:
            timestamp = self._validate_timestamp(timestamp)

        self._acquire_write()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                existing = self._get_memory_locked(conn, memory_id)
                if existing is None:
                    conn.rollback()
                    return False

                old_tags = existing.get_tag_list()

                # apply field changes onto the existing object
                if name is not None:
                    existing.name = name
                if content is not None:
                    existing.content = content
                if timestamp is not None:
                    existing.timestamp = timestamp
                if new_tags is not None:
                    existing.set_tag_list(new_tags)

                conn.execute(
                    "UPDATE memories "
                    "SET timestamp = ?, name = ?, content = ?, tags = ? "
                    "WHERE id = ?",
                    (existing.timestamp, existing.name, existing.content,
                     existing.tags, memory_id))

                # tag-table sync (only when tags changed)
                if new_tags is not None:
                    self._tags_replace_locked(conn, memory_id, old_tags, new_tags)

                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            self.lock.release_write()

    def delete_memory(self, memory_id: str) -> bool:
        """Deletes a memory and decrements/prunes its tags inside a single
        transaction under the WRITE lock. Returns True on success, False if the
        memory does not exist.
        """
        if not isinstance(memory_id, str) or len(memory_id) == 0:
            raise MembankInputError("\"id\" must be a non-empty string.")

        self._acquire_write()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                existing = self._get_memory_locked(conn, memory_id)
                if existing is None:
                    conn.rollback()
                    return False
                old_tags = existing.get_tag_list()
                conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                self._tags_remove_locked(conn, memory_id, old_tags)
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            self.lock.release_write()

    def rebuild_tags(self):
        """Recomputes the `tags`/`memory_tags` tables from the `memories` rows
        under the WRITE lock (self-healing / admin repair).
        """
        self._acquire_write()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                self._rebuild_tags_locked(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            self.lock.release_write()

    # ------------------- tag-table sync helpers (locked) ------------------- #
    # All of these assume the caller holds the write lock and an open
    # transaction; they never acquire the lock or commit themselves.
    def _tags_add_locked(self, conn, memory_id: str, tags: list):
        """Increments refcounts and inserts membership rows for ``tags``."""
        for tag in tags:
            conn.execute(
                "INSERT INTO tags (tag, refcount) VALUES (?, 1) "
                "ON CONFLICT(tag) DO UPDATE SET refcount = refcount + 1",
                (tag,))
            conn.execute(
                "INSERT OR IGNORE INTO memory_tags (memory_id, tag) "
                "VALUES (?, ?)", (memory_id, tag))

    def _tags_remove_locked(self, conn, memory_id: str, tags: list):
        """Decrements refcounts, removes membership rows, prunes orphans."""
        for tag in tags:
            conn.execute(
                "DELETE FROM memory_tags WHERE memory_id = ? AND tag = ?",
                (memory_id, tag))
            conn.execute(
                "UPDATE tags SET refcount = refcount - 1 WHERE tag = ?", (tag,))
        # prune any tags whose refcount dropped to zero (or below)
        conn.execute("DELETE FROM tags WHERE refcount <= 0")

    def _tags_replace_locked(self, conn, memory_id: str, old_tags: list,
                             new_tags: list):
        """Applies the delta between ``old_tags`` and ``new_tags`` for a memory."""
        old_set = set(old_tags)
        new_set = set(new_tags)
        added = [t for t in new_tags if t not in old_set]
        removed = [t for t in old_tags if t not in new_set]
        if removed:
            self._tags_remove_locked(conn, memory_id, removed)
        if added:
            self._tags_add_locked(conn, memory_id, added)

    def _rebuild_tags_locked(self, conn):
        """Truncates and recomputes the tag index from the memory rows."""
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM memory_tags")

        refcounts = {}
        cur = conn.execute("SELECT id, tags FROM memories")
        for memory_id, tags_json in cur.fetchall():
            try:
                parsed = json.loads(tags_json)
            except (json.JSONDecodeError, TypeError):
                parsed = []
            if not isinstance(parsed, list):
                parsed = []
            # de-dupe per memory (defensive; stored form is already de-duped)
            seen = set()
            for tag in parsed:
                if not isinstance(tag, str) or tag in seen:
                    continue
                seen.add(tag)
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) "
                    "VALUES (?, ?)", (memory_id, tag))
                refcounts[tag] = refcounts.get(tag, 0) + 1

        for tag, count in refcounts.items():
            conn.execute("INSERT INTO tags (tag, refcount) VALUES (?, ?)",
                         (tag, count))

    # ---------------------------- filter build ----------------------------- #
    def _build_filter_clause(self, filters: dict):
        """Builds a parameterized WHERE clause (without the ``WHERE`` keyword)
        and its bound parameters from a filter dict. Returns ``(sql, params)``
        where ``sql`` is ``""`` when there are no filters.
        """
        if not filters:
            return "", []
        if not isinstance(filters, dict):
            raise MembankInputError("\"filters\" must be an object.")

        clauses = []
        params = []

        # time range
        time_range = filters.get("time_range")
        if time_range is not None:
            if not isinstance(time_range, dict):
                raise MembankInputError("\"time_range\" must be an object.")
            start = time_range.get("start")
            end = time_range.get("end")
            if start is not None:
                if not isinstance(start, int) or isinstance(start, bool):
                    raise MembankInputError(
                        "\"time_range.start\" must be an integer epoch.")
                clauses.append("timestamp >= ?")
                params.append(start)
            if end is not None:
                if not isinstance(end, int) or isinstance(end, bool):
                    raise MembankInputError(
                        "\"time_range.end\" must be an integer epoch.")
                clauses.append("timestamp <= ?")
                params.append(end)

        # tags (sanitized before matching)
        tags = filters.get("tags")
        if tags:
            sanitized = sanitize_tags(tags)
            if sanitized:
                placeholders = ", ".join(["?"] * len(sanitized))
                mode = str(filters.get("tag_mode", "any")).lower()
                if mode == "all":
                    clauses.append(
                        "id IN (SELECT memory_id FROM memory_tags "
                        "WHERE tag IN (%s) GROUP BY memory_id "
                        "HAVING COUNT(DISTINCT tag) = ?)" % placeholders)
                    params.extend(sanitized)
                    params.append(len(sanitized))
                else:
                    clauses.append(
                        "id IN (SELECT memory_id FROM memory_tags "
                        "WHERE tag IN (%s))" % placeholders)
                    params.extend(sanitized)

        # keyword substring search (name + content), literal % / _.
        #
        # Two accepted, back-compatible shapes:
        #   * `keyword`  — a single substring (unchanged legacy contract used by
        #     telegram `/m search`); matched as one `(name LIKE OR content LIKE)`.
        #   * `keywords` — an optional list of substrings; each term contributes
        #     its own `(name LIKE OR content LIKE)` pair and the terms are OR'd
        #     together into ONE parenthesized group (match ANY term).
        # When both are present the effective term list is `[keyword] + keywords`
        # (de-duped, order-preserving). The resulting group is ANDed with the
        # time/tag clauses exactly as before (`" AND ".join(clauses)`), so a lone
        # `keyword` yields byte-for-byte identical SQL to the previous behavior.
        terms = []

        keyword = filters.get("keyword")
        if keyword is not None:
            if not isinstance(keyword, str):
                raise MembankInputError("\"keyword\" must be a string.")
            keyword = keyword.strip()
            if len(keyword) > 0:
                terms.append(keyword)

        keywords = filters.get("keywords")
        if keywords is not None:
            if not isinstance(keywords, list):
                raise MembankInputError("\"keywords\" must be a list.")
            for kw in keywords:
                if not isinstance(kw, str):
                    raise MembankInputError(
                        "\"keywords\" must be a list of strings.")
                kw = kw.strip()
                if len(kw) > 0:
                    terms.append(kw)

        # De-dupe while preserving order, then cap the number of terms actually
        # compiled into SQL so the statement stays bounded (upstream extraction
        # already de-dupes/caps; this is a defensive server-side mirror).
        seen = set()
        deduped = []
        for t in terms:
            if t in seen:
                continue
            seen.add(t)
            deduped.append(t)
        deduped = deduped[:KEYWORD_SQL_CAP]

        if deduped:
            ors = []
            for t in deduped:
                like = "%" + _like_escape(t) + "%"
                ors.append(
                    "(name LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
                params.append(like)
                params.append(like)
            # A single term reproduces the exact legacy clause (no extra
            # wrapping parens) so lone-`keyword` callers are byte-for-byte
            # unchanged; multiple terms are OR'd inside one group.
            if len(ors) == 1:
                clauses.append(ors[0])
            else:
                clauses.append("(" + " OR ".join(ors) + ")")

        return " AND ".join(clauses), params

    # ------------------------------ helpers -------------------------------- #
    @staticmethod
    def _now() -> int:
        """Returns the current time as unix epoch seconds (UTC)."""
        return int(datetime.now(timezone.utc).timestamp())

    @staticmethod
    def _validate_timestamp(timestamp) -> int:
        """Validates a client-supplied timestamp (epoch seconds int)."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise MembankInputError(
                "\"timestamp\" must be an integer epoch (seconds).")
        return timestamp

    @staticmethod
    def _normalize_limit(limit) -> int:
        """Clamps the requested page size to ``[1, LIST_LIMIT_CAP]`` with a
        default of ``LIST_LIMIT_DEFAULT``.
        """
        if limit is None:
            return LIST_LIMIT_DEFAULT
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise MembankInputError("\"limit\" must be an integer.")
        if limit <= 0:
            return LIST_LIMIT_DEFAULT
        return min(limit, LIST_LIMIT_CAP)

    @staticmethod
    def _normalize_offset(offset) -> int:
        """Normalizes the requested offset (>= 0)."""
        if offset is None:
            return 0
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise MembankInputError("\"offset\" must be an integer.")
        return max(offset, 0)


# =========================== MemoryBankRegistry ============================ #
class MemoryBankRegistry:
    """Builds and owns all `MemoryBank`s from config, enforcing id/db_path
    uniqueness. Banks are resolved by id only; the client never supplies a path.
    """
    def __init__(self):
        self._banks = {}    # id -> MemoryBank

    @classmethod
    def build(cls, bank_configs: list, lock_timeout=None):
        """Constructs a registry from a list of `MemoryBankConfig` objects,
        validating each bank's id and db_path. Raises `MembankConfigError` on any
        duplicate id, duplicate resolved db_path, or invalid id.

        Each bank's `db_path` is authoritative (it comes from TRUSTED admin
        config, not user input). The retained safeguards are: `realpath`-
        normalize every `db_path` and reject two banks that resolve to the SAME
        file (duplicate = fatal), enforce unique bank ids, and never build a
        filesystem path from a bank id. The parent directory of each `db_path`
        is auto-created (`os.makedirs(..., exist_ok=True)`) so the SQLite file
        can be created in the configured location.

        ``lock_timeout`` (seconds, or None for unbounded) is the service-level
        default applied to every bank whose own `MemoryBankConfig.lock_timeout`
        is unset (None), so a slow/hot bank fails secure with an HTTP 503
        instead of stalling the shared worker pool indefinitely.
        """
        registry = cls()
        seen_paths = {}     # realpath -> bank id

        for bank_cfg in bank_configs:
            bank_id = bank_cfg.id

            # 1. id syntax / path-safety (a bank id is never used to build a
            #    filesystem path, but is validated defensively regardless).
            if not isinstance(bank_id, str) or BANK_ID_RE.match(bank_id) is None:
                raise MembankConfigError(
                    "Invalid bank id \"%s\": bank ids must match %s." %
                    (bank_id, BANK_ID_RE.pattern))

            # 2. id uniqueness
            if bank_id in registry._banks:
                raise MembankConfigError(
                    "Duplicate bank id \"%s\": bank ids must be unique." %
                    bank_id)

            # 3. db_path uniqueness (after realpath). Two banks must never share
            #    the same underlying file.
            resolved = os.path.realpath(bank_cfg.db_path)
            if resolved in seen_paths:
                raise MembankConfigError(
                    "Duplicate db_path for bank \"%s\": already used by bank "
                    "\"%s\"." % (bank_id, seen_paths[resolved]))
            seen_paths[resolved] = bank_id

            # 4. Effective lock timeout: fall back to the service-level default
            #    when this bank did not specify its own.
            if bank_cfg.lock_timeout is None:
                bank_cfg.lock_timeout = lock_timeout

            # 5. Auto-create the parent directory of the configured db_path so
            #    the SQLite file can be created there. Only the parent of the
            #    configured path is created.
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)

            registry._banks[bank_id] = MemoryBank(bank_cfg)

        return registry

    def get(self, bank_id: str):
        """Returns the `MemoryBank` for the given id, or None."""
        return self._banks.get(bank_id, None)

    def all(self) -> list:
        """Returns all banks (order is insertion order)."""
        return list(self._banks.values())

    def readable_by(self, username: str) -> list:
        """Returns all banks the given user may read."""
        return [b for b in self._banks.values() if b.can_read(username)]

    def writable_by(self, username: str) -> list:
        """Returns all banks the given user may write."""
        return [b for b in self._banks.values() if b.can_write(username)]

    @staticmethod
    def _normalize_ref(ref) -> str:
        """Normalizes a natural-language bank reference for matching: lowercases,
        strips surrounding whitespace, and collapses internal whitespace runs to
        a single space. Returns "" if `ref` is None/empty.
        """
        if ref is None:
            return ""
        return " ".join(str(ref).strip().lower().split())

    def resolve_ref(self, ref, username: str, require_write: bool = False):
        """Resolves a natural-language bank reference to a single accessible
        `MemoryBank`, or None.

        The candidate pool is ACL-filtered up front: writable banks when
        `require_write` is True, otherwise readable banks. Matching against that
        pool proceeds in order (first hit wins), all case/whitespace-insensitive:

          1. Exact normalized **id**.
          2. Exact normalized **name** (human title).
          3. **Unique substring** — exactly one accessible bank whose normalized
             name contains, or is contained by, the normalized ref. If more than
             one bank matches, the reference is ambiguous and treated as
             UNRESOLVED (returns None) so the caller can ask to disambiguate.

        Returns the matched `MemoryBank` (already ACL-checked) or None when the
        reference is empty, unknown, inaccessible, or ambiguous.
        """
        norm_ref = self._normalize_ref(ref)
        if len(norm_ref) == 0:
            return None

        pool = (self.writable_by(username) if require_write
                else self.readable_by(username))
        if len(pool) == 0:
            return None

        # 1. Exact normalized id.
        for bank in pool:
            if self._normalize_ref(bank.config.id) == norm_ref:
                return bank

        # 2. Exact normalized name.
        for bank in pool:
            if self._normalize_ref(bank.config.name) == norm_ref:
                return bank

        # 3. Unique substring (bidirectional containment) against names.
        matches = []
        for bank in pool:
            norm_name = self._normalize_ref(bank.config.name)
            if len(norm_name) == 0:
                continue
            if norm_ref in norm_name or norm_name in norm_ref:
                matches.append(bank)
        if len(matches) == 1:
            return matches[0]

        # No unique match (zero or ambiguous) -> unresolved.
        return None

    def init_all_schemas(self):
        """Initializes the schema of every bank (creating DB files as needed)."""
        for bank in self._banks.values():
            os.makedirs(os.path.dirname(os.path.realpath(bank.config.db_path)),
                        exist_ok=True)
            bank.init_schema()
