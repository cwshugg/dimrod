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

    def acquire_read(self):
        """Acquire a read lock. Multiple threads can hold this
        simultaneously.
        """
        with self._read_ready:
            while self._writing or self._writers_waiting > 0:
                self._read_ready.wait()
            self._readers += 1

    def release_read(self):
        """Release a read lock."""
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self):
        """Acquire a write lock. Exclusive access — no other readers or
        writers.
        """
        with self._read_ready:
            self._writers_waiting += 1
            while self._readers > 0 or self._writing:
                self._read_ready.wait()
            self._writers_waiting -= 1
            self._writing = True

    def release_write(self):
        """Release a write lock."""
        with self._read_ready:
            self._writing = False
            self._read_ready.notify_all()
