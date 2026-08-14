# Worker-thread pool for the membank service.
#
# Modeled structurally after `grocer/threads.py`: the thread classes are thin
# loop wrappers, and all DB business logic lives in `MemoryBank`/`MembankService`
# methods that the workers invoke. The pool bounds the number of threads that
# touch SQLite at once, while the gevent WSGI server still accepts many
# simultaneous HTTP connections.
#
# How a request reaches a worker (architecture report §6.1): the Oracle handler
# builds a `Job` (a callable + args + a completion `Event`), puts it on the
# shared queue, and blocks on the result. A worker pops the job, runs the DB
# operation (which acquires the target bank's `ReadWriteLock` itself), and
# signals completion. Correctness rests on the per-bank lock, not the pool.
#
#   Connor Shugg

# Imports
import os
import sys
import queue
import threading

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local library imports
from lib.config import Config, ConfigField


# ============================= WorkerPoolConfig ============================= #
class WorkerPoolConfig(Config):
    """Configuration for the membank worker-thread pool.

    * `worker_count`   — number of daemon worker threads dispatching DB jobs.
    * `max_queue_size` — bound on jobs waiting for a free worker. When the pool
      is saturated, requests are rejected with a retryable HTTP 503 rather than
      queued without limit (so one hot bank can't exhaust shared capacity).
      0 = unbounded (the historical behavior).
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("worker_count",   [int], required=False, default=4),
            ConfigField("max_queue_size", [int], required=False, default=128),
        ]


# ============================ WorkerPoolSaturated =========================== #
class WorkerPoolSaturated(Exception):
    """Raised by `WorkerPool.submit` when the bounded work queue is full. The
    oracle maps this to a fail-secure, retryable HTTP 503 so that a burst of work
    (e.g. targeting one hot bank) sheds load instead of queueing without bound
    and exhausting capacity for unrelated banks.
    """
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ================================== Job ==================================== #
class Job:
    """A unit of work submitted to the worker pool: a callable plus its
    positional/keyword arguments and a result slot. The submitter blocks on
    `wait()` until a worker has run the callable.
    """
    def __init__(self, fn, args=(), kwargs=None):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs if kwargs is not None else {}
        self._event = threading.Event()
        self._result = None
        self._error = None

    def run(self):
        """Executes the job's callable, capturing its result or exception."""
        try:
            self._result = self.fn(*self.args, **self.kwargs)
        except BaseException as e:  # noqa: B902 — re-raised to the submitter
            self._error = e
        finally:
            self._event.set()

    def wait(self):
        """Blocks until the job has run, then returns its result or re-raises the
        exception it produced (so the caller sees failures on its own thread).
        """
        self._event.wait()
        if self._error is not None:
            raise self._error
        return self._result


# =============================== WorkerThread ============================== #
class WorkerThread(threading.Thread):
    """A thin daemon loop that pops `Job`s off the pool's queue and runs them.

    A `None` sentinel on the queue tells the worker to exit (used at shutdown).
    """
    def __init__(self, pool, name: str):
        super().__init__(name=name)
        self.pool = pool
        self.daemon = True

    def run(self):
        while True:
            job = self.pool.queue.get()
            try:
                if job is None:
                    # shutdown sentinel
                    return
                job.run()
            finally:
                self.pool.queue.task_done()


# ================================ WorkerPool =============================== #
class WorkerPool:
    """A fixed pool of daemon worker threads dispatching DB jobs off a shared,
    thread-safe queue.
    """
    def __init__(self, config: "WorkerPoolConfig", log=None):
        self.config = config
        self.worker_count = max(1, int(config.worker_count))
        # A bounded queue sheds load (fail-secure 503) instead of queueing
        # without limit. `max_queue_size <= 0` means unbounded (historical
        # behavior); a positive value caps the number of jobs that may wait for a
        # free worker before `submit` rejects with `WorkerPoolSaturated`.
        self.max_queue_size = int(config.max_queue_size)
        maxsize = self.max_queue_size if self.max_queue_size > 0 else 0
        self.queue = queue.Queue(maxsize=maxsize)
        self.log = log
        self.workers = []
        self._started = False

    def start(self):
        """Creates and starts the worker threads (idempotent)."""
        if self._started:
            return
        for i in range(self.worker_count):
            worker = WorkerThread(self, name="membank-worker-%d" % i)
            worker.start()
            self.workers.append(worker)
            if self.log is not None:
                self.log.write("Started worker thread: %s" % worker.name)
        self._started = True

    def submit(self, fn, *args, **kwargs):
        """Submits a callable to the pool and BLOCKS until it completes, then
        returns its result (or re-raises its exception). If the pool has not been
        started, the callable runs inline on the calling thread (useful for
        tests and for graceful degradation).

        When the pool is started and its queue is bounded (``max_queue_size >
        0``), a full queue causes an immediate `WorkerPoolSaturated` (mapped to a
        fail-secure, retryable HTTP 503) rather than an unbounded wait — so one
        hot bank cannot exhaust shared capacity for unrelated banks.
        """
        job = Job(fn, args=args, kwargs=kwargs)
        if not self._started:
            job.run()
            return job.wait()
        try:
            # Non-blocking put: reject (503) rather than queue without bound.
            self.queue.put(job, block=False)
        except queue.Full:
            if self.log is not None:
                self.log.write("Worker pool saturated; rejecting job (503).")
            raise WorkerPoolSaturated(
                "The service is busy; please retry shortly.")
        return job.wait()

    def shutdown(self):
        """Signals all workers to exit and waits for the queue to drain."""
        if not self._started:
            return
        for _ in self.workers:
            self.queue.put(None)
        for worker in self.workers:
            worker.join(timeout=5)
        self._started = False
