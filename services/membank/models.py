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
#                            id/db_path uniqueness and path-safety.
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


# =============================== Input Limits =============================== #
# Bounds on user-supplied input (architecture report §9.6 / Q-4). These keep DB
# growth and abusive payloads in check; they are generous for real notes.
NAME_MAX_LEN = 256              # max characters in a memory `name`
CONTENT_MAX_LEN = 64 * 1024     # max bytes/characters in a memory `content`
TAGS_MAX_COUNT = 32             # max distinct tags per memory
TAG_MAX_LEN = 64                # max characters in a single (sanitized) tag
LIST_LIMIT_DEFAULT = 100        # default page size for /memory/list
LIST_LIMIT_CAP = 500            # hard cap for the page size

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


# =============================== MemoryBank ================================ #
class MemoryBank:
    """A single memory bank: one SQLite database file with a unique id, a human
    name, a read/write ACL, and its own in-process `ReadWriteLock`.

    All DB access opens a short-lived connection nested inside the lock's
    critical section; no connection is cached or shared across threads.
    """
    def __init__(self, bank_id: str, name: str, db_path: str,
                 read_users: list, write_users: list, lock_timeout=None):
        self.id = bank_id
        self.name = name
        self.db_path = db_path
        self.read_users = list(read_users)
        self.write_users = list(write_users)
        self.lock = ReadWriteLock()
        # Bounded wait (seconds) for acquiring this bank's lock on a request. A
        # value of None means an unbounded wait (the historical behavior). When
        # set, a timed-out acquisition raises `MembankLockTimeout` -> HTTP 503
        # instead of blocking forever, so one slow/hot bank cannot stall others.
        self.lock_timeout = lock_timeout

    # -------------------------- lock acquisition --------------------------- #
    def _acquire_read(self):
        """Acquires this bank's read lock, honoring `lock_timeout`. Raises
        `MembankLockTimeout` (mapped to 503) if the timeout elapses; in that case
        no lock is held and the caller must NOT release it.
        """
        if not self.lock.acquire_read(timeout=self.lock_timeout):
            raise MembankLockTimeout(
                "bank \"%s\" is busy; try again shortly." % self.id)

    def _acquire_write(self):
        """Acquires this bank's write lock, honoring `lock_timeout`. Raises
        `MembankLockTimeout` (mapped to 503) if the timeout elapses; in that case
        no lock is held, no DB work runs, and no partial write occurs.
        """
        if not self.lock.acquire_write(timeout=self.lock_timeout):
            raise MembankLockTimeout(
                "bank \"%s\" is busy; try again shortly." % self.id)

    # ------------------------------- ACL ----------------------------------- #
    def can_read(self, username: str) -> bool:
        """Returns True if the given user may read/list/query this bank."""
        return username in self.read_users

    def can_write(self, username: str) -> bool:
        """Returns True if the given user may create/update/delete in this bank."""
        return username in self.write_users

    # --------------------------- connections ------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        """Opens a fresh SQLite connection to this bank's DB file with WAL
        journaling enabled. Callers MUST already hold this bank's lock and MUST
        close the connection when done.
        """
        conn = sqlite3.connect(self.db_path)
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

        # keyword substring (name + content), literal % / _
        keyword = filters.get("keyword")
        if keyword is not None:
            if not isinstance(keyword, str):
                raise MembankInputError("\"keyword\" must be a string.")
            keyword = keyword.strip()
            if len(keyword) > 0:
                like = "%" + _like_escape(keyword) + "%"
                clauses.append(
                    "(name LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
                params.append(like)
                params.append(like)

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
    uniqueness and path-safety. Banks are resolved by id only; the client never
    supplies a path.
    """
    def __init__(self):
        self._banks = {}    # id -> MemoryBank

    @classmethod
    def build(cls, bank_configs: list, db_dir: str, lock_timeout=None):
        """Constructs a registry from a list of `MemoryBankConfig` objects,
        validating each bank's id and db_path. Raises `MembankConfigError` on any
        duplicate id, duplicate resolved db_path, invalid id, or path-safety
        violation (path outside ``db_dir``).

        ``lock_timeout`` (seconds, or None for unbounded) is applied to every
        bank's `ReadWriteLock` acquisition so a slow/hot bank fails secure with
        an HTTP 503 instead of stalling the shared worker pool indefinitely.
        """
        registry = cls()
        anchor = os.path.realpath(db_dir)
        seen_paths = {}     # realpath -> bank id

        for bank_cfg in bank_configs:
            bank_id = bank_cfg.id

            # 1. id syntax / path-safety
            if not isinstance(bank_id, str) or BANK_ID_RE.match(bank_id) is None:
                raise MembankConfigError(
                    "Invalid bank id \"%s\": bank ids must match %s." %
                    (bank_id, BANK_ID_RE.pattern))

            # 2. id uniqueness
            if bank_id in registry._banks:
                raise MembankConfigError(
                    "Duplicate bank id \"%s\": bank ids must be unique." %
                    bank_id)

            # 3. db_path safety — resolve and ensure it lives within db_dir
            resolved = os.path.realpath(bank_cfg.db_path)
            if resolved != anchor and not resolved.startswith(anchor + os.sep):
                raise MembankConfigError(
                    "Unsafe db_path for bank \"%s\": resolved path escapes the "
                    "configured db_dir." % bank_id)

            # 4. db_path uniqueness (after realpath)
            if resolved in seen_paths:
                raise MembankConfigError(
                    "Duplicate db_path for bank \"%s\": already used by bank "
                    "\"%s\"." % (bank_id, seen_paths[resolved]))
            seen_paths[resolved] = bank_id

            registry._banks[bank_id] = MemoryBank(
                bank_id=bank_id,
                name=bank_cfg.name,
                db_path=bank_cfg.db_path,
                read_users=bank_cfg.read_users,
                write_users=bank_cfg.write_users,
                lock_timeout=lock_timeout,
            )

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

    def init_all_schemas(self):
        """Initializes the schema of every bank (creating DB files as needed)."""
        for bank in self._banks.values():
            os.makedirs(os.path.dirname(os.path.realpath(bank.db_path)),
                        exist_ok=True)
            bank.init_schema()
