# This module implements warden's background job system: a small, thread-safe
# worker pool that executes network jobs (range/port/OS scans, ARP-poison
# blocks, and full network sweeps) OFF the main service thread.
#
# The design is intentionally decoupled from warden's networking logic: this
# module owns the `Job` data model and the `JobManager` (queue + worker pool +
# registry + TTL eviction), while the actual work for each job type is supplied
# by the caller as a handler callable registered by job type. `WardenService`
# registers handlers that touch its cache / DB / lib wrappers.
#
# Concurrency model (async):
#   * `submit(type, params)` enqueues a job and immediately returns its id.
#   * A fixed pool of N daemon worker threads pull jobs off a `queue.Queue`,
#     transition status (pending -> running -> done/failed), capture exceptions
#     into `job.error`, and store results on the job.
#   * Callers poll `get(job_id)` / `list()` for status/result/error.
#   * Finished jobs are retained for a configurable TTL, then evicted.
#
#   Connor Shugg (Byteboy)

# Imports
import uuid
import queue
import threading
from datetime import datetime


# ================================ Job Types ================================ #
class JobType:
    """Enumeration of the supported background job types (string constants so
    they serialize cleanly to JSON and are easy to match on).
    """
    SCAN_RANGE = "scan_range"
    SCAN_PORTS = "scan_ports"
    DETECT_OS = "detect_os"
    ARPPOISON = "arppoison"
    NETWORK_SWEEP = "network_sweep"

    # the complete set of valid job types
    ALL = (SCAN_RANGE, SCAN_PORTS, DETECT_OS, ARPPOISON, NETWORK_SWEEP)

    @classmethod
    def is_valid(cls, job_type: str) -> bool:
        """Returns True if `job_type` is a known job type."""
        return job_type in cls.ALL


# =============================== Job Statuses ============================== #
class JobStatus:
    """Enumeration of the lifecycle states a job passes through."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # statuses that indicate a job has reached a terminal state
    TERMINAL = (DONE, FAILED, CANCELLED)


# ================================== Job ==================================== #
class Job:
    """A single background job. Holds its type, submitted parameters, lifecycle
    status, result/error, and timestamps. `to_json()` yields an API-friendly
    dict for status polling.
    """

    def __init__(self, job_type: str, params: dict = None):
        """Constructor. Assigns a fresh uuid4 hex id and marks the job pending."""
        self.id = uuid.uuid4().hex
        self.type = job_type
        self.params = dict(params) if params else {}
        self.status = JobStatus.PENDING
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.started_at = None
        self.finished_at = None
        # a per-job cooperative-cancel event; long-running handlers (e.g. the
        # ARP-poison block) can watch this / a deadline to stop early
        self.cancel_event = threading.Event()

    def is_terminal(self) -> bool:
        """Returns True if the job has reached a terminal (done/failed/cancelled)
        state.
        """
        return self.status in JobStatus.TERMINAL

    @staticmethod
    def _iso(dt):
        """Serializes a datetime to an ISO string (or None)."""
        return dt.isoformat() if isinstance(dt, datetime) else None

    def to_json(self) -> dict:
        """Serializes the job to a JSON-safe dict for API responses."""
        return {
            "id": self.id,
            "type": self.type,
            "params": self.params,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self._iso(self.created_at),
            "started_at": self._iso(self.started_at),
            "finished_at": self._iso(self.finished_at),
        }


# ============================== Job Manager =============================== #
class JobManager:
    """A thread-safe queue plus a pool of N daemon worker threads that execute
    jobs. Work for each job type is performed by a registered handler callable
    `handler(job) -> result` (raising on failure). The manager owns the job
    registry, status transitions, exception capture, and TTL-based eviction of
    finished jobs.
    """

    def __init__(self, num_workers: int = 3, result_ttl: int = 3600, log=None):
        """Constructor.

        Arguments:
          num_workers  Number of worker threads to spawn (must be > 0).
          result_ttl   Seconds a finished job is retained before eviction.
          log          Optional logger with a `write(msg)` method.
        """
        assert num_workers > 0, "the job worker count must be greater than 0"
        self.num_workers = num_workers
        self.result_ttl = result_ttl
        self.log = log

        self._queue = queue.Queue()
        self._jobs = {}                     # id -> Job (the registry)
        self._handlers = {}                 # job_type -> callable(job)
        self._lock = threading.Lock()       # guards _jobs and _handlers
        self._workers = []
        self._shutdown = threading.Event()
        self._started = False

    # ------------------------------ Logging --------------------------------- #
    def _log(self, msg: str):
        """Writes a message to the log, if one is present."""
        if self.log is not None:
            self.log.write(msg)

    # --------------------------- Handler Registry --------------------------- #
    def register(self, job_type: str, handler):
        """Registers a handler callable for a job type. `handler(job)` should
        perform the work and return a JSON-serializable result (or raise on
        failure).
        """
        with self._lock:
            self._handlers[job_type] = handler

    # ------------------------------ Lifecycle ------------------------------- #
    def start(self):
        """Spawns the worker-thread pool (idempotent). Threads are daemons so
        they never block interpreter shutdown.
        """
        if self._started:
            return
        self._started = True
        for i in range(self.num_workers):
            t = threading.Thread(target=self._worker_loop,
                                 name="warden-job-worker-%d" % i,
                                 daemon=True)
            t.start()
            self._workers.append(t)
        self._log("Started %d job worker(s)." % self.num_workers)

    def stop(self, wait: bool = False):
        """Signals all workers to shut down. If `wait` is True, blocks until the
        worker threads exit. Mostly useful for tests and clean teardown.
        """
        self._shutdown.set()
        # wake any workers blocked on an empty queue with sentinels
        for _ in range(self.num_workers):
            self._queue.put(None)
        if wait:
            for t in self._workers:
                t.join(timeout=5.0)

    # ------------------------------ Submission ------------------------------ #
    def submit(self, job_type: str, params: dict = None) -> str:
        """Creates a job, registers it, enqueues it, and returns its id.

        Raises:
          ValueError  If `job_type` is not a known job type.
        """
        if not JobType.is_valid(job_type):
            raise ValueError("unknown job type '%s'" % job_type)
        job = Job(job_type, params)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job)
        return job.id

    # ------------------------------- Lookups -------------------------------- #
    def get(self, job_id: str):
        """Returns the `Job` with the given id, or None."""
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list:
        """Returns a snapshot list of all currently-retained jobs, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def has_active(self, job_type: str) -> bool:
        """Returns True if a job of the given type is currently pending or
        running. Used to avoid stacking overlapping sweeps.
        """
        with self._lock:
            for job in self._jobs.values():
                if job.type == job_type and \
                   job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                    return True
        return False

    # ------------------------------- Workers -------------------------------- #
    def _worker_loop(self):
        """Main loop for a single worker thread: pull jobs, execute them, and
        periodically evict expired finished jobs.
        """
        while not self._shutdown.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._evict_expired()
                continue
            # a None sentinel signals shutdown
            if job is None:
                self._queue.task_done()
                break
            try:
                self._run_job(job)
            finally:
                self._queue.task_done()
                self._evict_expired()

    def _run_job(self, job: Job):
        """Executes a single job, transitioning its status and capturing any
        exception into `job.error` with status `failed`.
        """
        with self._lock:
            handler = self._handlers.get(job.type)
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        try:
            if handler is None:
                raise ValueError("no handler registered for job type '%s'"
                                 % job.type)
            job.result = handler(job)
            job.status = JobStatus.DONE
        except Exception as e:
            job.error = str(e) or e.__class__.__name__
            job.status = JobStatus.FAILED
            self._log("Job %s (%s) failed: %s" % (job.id, job.type, job.error))
        finally:
            job.finished_at = datetime.now()

    def _evict_expired(self):
        """Removes finished jobs whose `finished_at` is older than `result_ttl`
        seconds from the registry.
        """
        if self.result_ttl is None or self.result_ttl <= 0:
            return
        cutoff = datetime.now().timestamp() - self.result_ttl
        with self._lock:
            expired = [
                jid for jid, job in self._jobs.items()
                if job.is_terminal() and job.finished_at is not None
                and job.finished_at.timestamp() < cutoff
            ]
            for jid in expired:
                del self._jobs[jid]
