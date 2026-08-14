# This module defines shared synchronization primitives used across the
# services.
#
# Currently it provides a single primitive: `ReadWriteLock`, a writer-priority,
# non-reentrant readers-writer lock. It was originally implemented inside the
# grocer service (`services/grocer/grocer.py`) and was promoted here so that
# multiple services (e.g. grocer and speaker) can share a single, well-tested
# implementation instead of duplicating a subtle concurrency primitive.
#
#   Connor Shugg

# Imports
import threading
import time
from contextlib import contextmanager


# ================================ LockTimeout =============================== #
class LockTimeout(Exception):
    """Raised by the ``read_locked``/``write_locked`` context managers when the
    lock could not be acquired within the requested ``timeout``. Callers that
    use the lower-level ``acquire_read``/``acquire_write`` methods get a
    ``False`` return instead (and can map it to whatever error they prefer).
    """
    pass


# ============================== ReadWriteLock =============================== #
class ReadWriteLock:
    """A readers-writer lock. Multiple readers can hold the lock concurrently,
    but a writer gets exclusive access. Writers are given priority to prevent
    starvation.

    This lock is **non-reentrant**: a thread that already holds the write lock
    must not call `acquire_write()` (or `acquire_read()`) again, and a thread
    holding the read lock must not attempt to upgrade to the write lock — doing
    either will deadlock. Callers cope with this by factoring multi-statement
    critical sections into helper methods that assume the lock is already held.
    """

    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writing = False

    def acquire_read(self, timeout=None):
        """Acquire a read lock. Multiple threads can hold this
        simultaneously.

        ``timeout`` (seconds) bounds how long the caller will wait for the lock.
        The default of ``None`` means no timeout (and the thread will block
        forever).

        When a ``timeout`` is supplied, the method returns ``True`` if the lock
        was acquired and ``False`` if the timeout elapsed first (in which case
        NO lock is held and the caller must not release it).
        """
        with self._read_ready:
            if timeout is None:
                while self._writing or self._writers_waiting > 0:
                    self._read_ready.wait()
            else:
                deadline = time.monotonic() + timeout
                while self._writing or self._writers_waiting > 0:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._read_ready.wait(remaining)
            self._readers += 1
            return True

    def release_read(self):
        """Release a read lock."""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self, timeout=None):
        """Acquire a write lock. Exclusive access — no other readers or
        writers.

        ``timeout`` (seconds) bounds how long the caller will wait for the lock.
        The default of ``None`` preserves the historical behavior: an unbounded
        wait that always succeeds. When a ``timeout`` is supplied, the method
        returns ``True`` if the lock was acquired and ``False`` if the timeout
        elapsed first (in which case NO lock is held and the caller must not
        release it).
        """
        with self._read_ready:
            self._writers_waiting += 1
            if timeout is None:
                while self._readers > 0 or self._writing:
                    self._read_ready.wait()
            else:
                deadline = time.monotonic() + timeout
                while self._readers > 0 or self._writing:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # Give up: stop advertising a pending writer and wake any
                        # readers that were parked solely on our behalf so they
                        # are not starved by an abandoned write request.
                        self._writers_waiting -= 1
                        self._read_ready.notify_all()
                        return False
                    self._read_ready.wait(remaining)
            self._writers_waiting -= 1
            self._writing = True
            return True

    def release_write(self):
        """Release a write lock."""
        with self._read_ready:
            self._writing = False
            self._read_ready.notify_all()

    # --------------------------- context managers -------------------------- #
    @contextmanager
    def read_locked(self, timeout=None):
        """Context manager acquiring the read lock for the duration of the block
        and releasing it on exit. Raises ``LockTimeout`` if ``timeout`` elapses
        before the lock is acquired (nothing is held in that case).
        """
        if not self.acquire_read(timeout=timeout):
            raise LockTimeout("timed out acquiring read lock")
        try:
            yield self
        finally:
            self.release_read()

    @contextmanager
    def write_locked(self, timeout=None):
        """Context manager acquiring the write lock for the duration of the
        block and releasing it on exit. Raises ``LockTimeout`` if ``timeout``
        elapses before the lock is acquired (nothing is held in that case).
        """
        if not self.acquire_write(timeout=timeout):
            raise LockTimeout("timed out acquiring write lock")
        try:
            yield self
        finally:
            self.release_write()
