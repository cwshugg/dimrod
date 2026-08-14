#!/usr/bin/python3
# The membank service stores plaintext notes ("memories") in one or more memory
# banks (SQLite databases) and exposes an authenticated, ACL-guarded, explicit
# HTTP API for creating, retrieving, updating, deleting, and filtering them.
#
# All natural-language understanding lives in the NLA/Telegram layer (a later
# phase) — never in this service. Membank receives only explicit values.
#
# See the architecture report `dba181c2549c113f` for the full design.
#
#   Connor Shugg

# Imports
import os
import sys
import time
import flask

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Enable import from this service's directory (for `models` / `threads`)
sdir = os.path.dirname(os.path.realpath(__file__))
if sdir not in sys.path:
    sys.path.append(sdir)

# Local library imports
from lib.config import ConfigField
from lib.service import Service, ServiceConfig
from lib.oracle import Oracle
from lib.cli import ServiceCLI
from lib.dialogue import DialogueConfig, DialogueInterface

# Local service imports
from models import (
    MemoryBankRegistry,
    MemoryBankConfig,
    MembankInputError,
    MembankConfigError,
    MembankLockTimeout,
)
from threads import WorkerPool, WorkerPoolConfig, WorkerPoolSaturated
from nla import (
    extract_store_fields,
    extract_query_filters,
    general_answer,
    nla_remember,
    nla_recall,
    NLA_REMEMBER_NAME,
    NLA_REMEMBER_DESC,
    NLA_RECALL_NAME,
    NLA_RECALL_DESC,
    _clean_bank,
    _resolve_default_bank,
    _need_bank_result,
    _render_bank_catalog,
)
from lib.nla import NLAEndpoint


# ================================== Config ================================= #
class MembankConfig(ServiceConfig):
    """Configuration for the membank service (extends `ServiceConfig`)."""
    def __init__(self):
        super().__init__()
        self.fields += [
            ConfigField("banks",        [MemoryBankConfig], required=True),
            # Worker-thread pool settings (thread count + bounded queue) nested
            # under a single `worker_pool` block. Optional: when omitted, the
            # service uses the `WorkerPoolConfig` defaults (4 workers / queue of
            # 128).
            ConfigField("worker_pool",  [WorkerPoolConfig], required=False,
                        default=None),
            # `lock_timeout`: the SERVICE-LEVEL default bounded wait (seconds)
            # for a bank's per-bank ReadWriteLock. On timeout the request fails
            # secure with HTTP 503 instead of blocking a worker indefinitely.
            # `null`/0 = unbounded (the historical behavior). Each bank whose own
            # `lock_timeout` is unset inherits this default (see
            # `MemoryBankRegistry.build`).
            ConfigField("lock_timeout", [float, int], required=False,
                        default=10.0),
            # Service-level DEFAULT memory bank id, used by the NLA layer when a
            # request neither names a bank NOR carries a per-request default
            # (telegram per-chat). A bank *id* (must be one of `banks[].id`).
            # Existence is validated fatally at STARTUP; per-user accessibility
            # is validated at REQUEST time (the invoking service account must be
            # able to read/write it for the action). Omit/null to disable.
            ConfigField("default_bank", [str], required=False, default=None),
            # The dialogue (LLM) config used ONLY by the NLA layer to extract
            # structured store/query parameters from natural-language input.
            # Optional so the core service can run without an LLM; the NLA
            # endpoints require it to be present.
            ConfigField("dialogue", [DialogueConfig], required=False,
                        default=None),
        ]


# ============================== Service Class ============================== #
class MembankService(Service):
    """Main service class for membank.

    The service thread validates config, resolves/creates the bank DBs (schema
    init under each bank's write lock), builds the `MemoryBankRegistry`
    (enforcing id/path uniqueness), starts a fixed worker pool, then sleeps
    forever (the Oracle + worker threads do the real work).
    """
    def __init__(self, config_path):
        super().__init__(config_path)
        self.config = MembankConfig()
        self.config.parse_file(config_path)

        # Build the registry from config. A duplicate id / duplicate resolved
        # db_path / unsafe path is a fatal config error — the service refuses to
        # start (the exception propagates out of the constructor).
        #
        # `lock_timeout` is the SERVICE-LEVEL default that bounds each per-bank
        # lock acquisition (fail-secure 503 on timeout). A null/0 value means
        # unbounded (historical behavior). Each bank whose own config does not
        # set `lock_timeout` inherits this default in `build`.
        lock_timeout = self.config.lock_timeout
        if not lock_timeout or lock_timeout <= 0:
            lock_timeout = None
        self.registry = MemoryBankRegistry.build(self.config.banks,
                                                  lock_timeout=lock_timeout)

        # Validate the service-level default bank id (existence only — this is
        # user-independent and can be checked at boot). Per-user accessibility
        # is enforced at request time by `MembankOracle.resolve_nla_bank`. An
        # unknown id is a fatal config error (fail fast, mirroring the
        # duplicate-id behavior above).
        if self.config.default_bank is not None:
            if self.registry.get(self.config.default_bank) is None:
                raise MembankConfigError(
                    "Invalid default_bank \"%s\": no bank with that id is "
                    "configured." % self.config.default_bank)

        # Fixed worker-thread pool (started in run()). The queue is bounded by
        # `worker_pool.max_queue_size` so a saturated pool sheds load with a
        # retryable 503 instead of queueing without limit (0 = unbounded /
        # historical). When no `worker_pool` block is configured, fall back to
        # the `WorkerPoolConfig` defaults.
        worker_pool = self.config.worker_pool
        if worker_pool is None:
            worker_pool = WorkerPoolConfig()
            worker_pool.init_defaults()
        self.pool = WorkerPool(worker_pool, log=self.log)

        # Lazily-constructed dialogue interface for the NLA layer (see
        # get_dialogue()). Only built on first NLA use.
        self._dialogue = None

    def run(self):
        """Overridden main function. Initializes all bank schemas, starts the
        worker pool, then sleeps forever.
        """
        super().run()

        # Resolve/create each bank DB and initialize its schema (under the
        # bank's write lock).
        try:
            self.registry.init_all_schemas()
            self.log.write("Initialized schemas for %d bank(s)." %
                           len(self.registry.all()))
        except Exception as e:
            self.log.write("Failed to initialize bank schemas: %s" % str(e))
            raise

        # Start the worker pool.
        self.pool.start()
        self.log.write("Worker pool started with %d worker(s)." %
                       self.pool.worker_count)

        # Sleep forever — the Oracle and worker threads handle all work.
        while True:
            time.sleep(60)

    def dispatch(self, fn, *args, **kwargs):
        """Dispatches a DB operation onto the worker pool and blocks until it
        completes, returning its result (or re-raising its exception). The
        callable acquires the target bank's `ReadWriteLock` itself.
        """
        return self.pool.submit(fn, *args, **kwargs)

    # ------------------------------- NLA / LLM ----------------------------- #
    def get_dialogue(self) -> DialogueInterface:
        """Lazily builds (and caches) the `DialogueInterface` used by the NLA
        layer. Raises `MembankInputError` if no dialogue config was provided.
        """
        if self.config.dialogue is None:
            raise MembankInputError(
                "The membank NLA layer requires a `dialogue` config block.")
        if self._dialogue is None:
            self._dialogue = DialogueInterface(self.config.dialogue)
        return self._dialogue

    def nla_extract_store(self, text: str, existing_tags: list = None) -> dict:
        """Uses the LLM to extract explicit STORE fields (`name`, `content`,
        `tags`, optional `bank`) from a natural-language "remember this" message.

        `existing_tags` (if provided) are the target bank's current tags,
        surfaced to the LLM as reuse suggestions (never changes what is stored).

        Returns the parsed dict. Raises on unrecoverable LLM/parse failure.
        """
        return extract_store_fields(self.get_dialogue(), text,
                                    existing_tags=existing_tags)

    def nla_extract_query(self, text: str, now_ts: int = None) -> dict:
        """Uses the LLM to convert a natural-language recall question into
        structured filters (`tags`, `time_range`, `keyword`, `tag_mode`,
        optional `bank`).

        Returns the parsed dict. Raises on unrecoverable LLM/parse failure.
        """
        return extract_query_filters(self.get_dialogue(), text, now_ts=now_ts)

    def nla_general_answer(self, text: str) -> str:
        """Runs a quick, general-purpose LLM completion that answers the user's
        question concisely, using the SAME NLA dialogue as the extraction calls.

        Used by `nla_recall` on a MISS to answer the user's original question
        directly. Returns the raw LLM text. Raises on unrecoverable LLM failure.
        """
        return general_answer(self.get_dialogue(), text)


# ============================== Service Oracle ============================= #
class MembankOracle(Oracle):
    """HTTP oracle for the membank service. Performs auth + per-bank ACL before
    dispatching any work, and enforces the ACL server-side on every endpoint.
    """

    # ------------------------------ ACL helpers ---------------------------- #
    def _acl_read(self, bank_id):
        """Resolves a bank for a READ operation. Returns the `MemoryBank` if the
        authenticated caller may read it, else None (caller returns 404 so the
        bank's existence is never confirmed to unauthorized users).
        """
        if not isinstance(bank_id, str) or len(bank_id) == 0:
            return None
        username = flask.g.user.config.username
        bank = self.service.registry.get(bank_id)
        if bank is None or not bank.can_read(username):
            return None
        return bank

    def _acl_write(self, bank_id):
        """Resolves a bank for a WRITE operation. Returns ``(bank, status)``:
          * ``(bank, 0)``     — caller may write
          * ``(None, 404)``   — bank missing or not readable (hidden)
          * ``(None, 403)``   — readable but not writable
        """
        if not isinstance(bank_id, str) or len(bank_id) == 0:
            return None, 404
        username = flask.g.user.config.username
        bank = self.service.registry.get(bank_id)
        if bank is None or not bank.can_read(username):
            return None, 404
        if not bank.can_write(username):
            return None, 403
        return bank, 0

    # ------------------------- NLA bank resolution ------------------------- #
    def resolve_nla_bank(self, params, named_ref, require_write):
        """Shared NLA bank resolver enforcing the locked precedence chain:

          (a) a bank named in the utterance (NL-resolved via `resolve_ref`,
              ACL-filtered for the invoking account) ->
          (b) the per-request default (`request_data.membank.default_bank`,
              telegram per-chat) ->
          (c) the service-level default (`config.default_bank`) ->
          (d) otherwise a "which bank?" clarification (listing accessible banks).

        Special rule for (a): if the utterance NAMED a bank but it does not
        resolve to an accessible bank, we return a clarification listing the
        accessible banks rather than silently falling through to (b)/(c) — that
        would risk reading from / writing to the wrong bank. Fallthrough to
        (b)/(c)/(d) only happens when the utterance named NO bank at all.

        The (b)/(c) defaults are resolved through the same ACL-filtered
        `resolve_ref`, so a configured default that is inaccessible to the
        invoking account is skipped (accessibility is a per-user, request-time
        property that cannot be fully validated at startup).

        Returns ``(bank, None)`` on success or ``(None, error_result)`` where
        `error_result` is a ready-to-return `NLAResult` clarification.
        """
        username = flask.g.user.config.username
        action = "save this to" if require_write else "search"
        accessible = (self.service.registry.writable_by(username)
                      if require_write
                      else self.service.registry.readable_by(username))

        # (a) explicit bank named in the utterance.
        ref = _clean_bank(named_ref)
        if ref is not None:
            bank = self.service.registry.resolve_ref(ref, username,
                                                     require_write=require_write)
            if bank is not None:
                return bank, None
            # Named but unresolved -> clarify; do NOT fall through to defaults.
            return None, _need_bank_result(action, banks=accessible)

        # (b) per-request default, then (c) service-level default. Each is
        # resolved through the ACL-filtered resolver; the first accessible hit
        # wins. An inaccessible/unknown default is skipped.
        for candidate in (_resolve_default_bank(params),
                          self.service.config.default_bank):
            candidate = _clean_bank(candidate)
            if candidate is None:
                continue
            bank = self.service.registry.resolve_ref(
                candidate, username, require_write=require_write)
            if bank is not None:
                return bank, None

        # (d) nothing resolved.
        return None, _need_bank_result(action, banks=accessible)

    # ---------------------------- request helpers -------------------------- #
    @staticmethod
    def _get_bank_id(jdata):
        """Extracts the required ``bank`` id from request JSON, or None."""
        if not isinstance(jdata, dict):
            return None
        return jdata.get("bank", None)

    # Exceptions that signal transient overload and must fail secure with a
    # retryable HTTP 503 (lock-acquire timeout or a saturated worker pool).
    _BUSY_ERRORS = (MembankLockTimeout, WorkerPoolSaturated)

    def _busy_response(self, where, err):
        """Builds the standard fail-secure 503 response for an overload
        condition (lock-acquire timeout or saturated pool), logging a
        content-free, retryable message.
        """
        self.log.write("Service busy in %s: %s" % (where, str(err)))
        return self.make_response(
            msg="The service is busy; please retry shortly.",
            success=False, rstatus=503)

    # ------------------------------ endpoints ------------------------------ #
    def endpoints(self):
        """Register all Oracle HTTP endpoints."""
        super().endpoints()

        # ------------------------------------------------------------------ #
        # POST /bank/list — banks the caller may READ (ACL-filtered)
        # ------------------------------------------------------------------ #
        @self.server.route("/bank/list", methods=["POST"])
        def endpoint_bank_list():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            username = flask.g.user.config.username
            try:
                banks = []
                for bank in self.service.registry.readable_by(username):
                    count = self.service.dispatch(bank.count_memories)
                    banks.append({
                        "id": bank.config.id,
                        "name": bank.config.name,
                        "can_write": bank.can_write(username),
                        "memory_count": count,
                    })
                return self.make_response(payload={"banks": banks})
            except self._BUSY_ERRORS as e:
                return self._busy_response("/bank/list", e)
            except Exception as e:
                self.log.write("Error in /bank/list: %s" % str(e))
                return self.make_response(msg="Failed to list banks.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /memory/list — filtered, paginated memory listing (read)
        # ------------------------------------------------------------------ #
        @self.server.route("/memory/list", methods=["POST"])
        def endpoint_memory_list():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank = self._acl_read(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=404)

            filters = jdata.get("filters", None)
            limit = jdata.get("limit", None)
            offset = jdata.get("offset", 0)
            order = jdata.get("order", "desc")

            try:
                memories, total = self.service.dispatch(
                    bank.list_memories, filters=filters, limit=limit,
                    offset=offset, order=order)
                payload = {
                    "bank": bank.config.id,
                    "count": len(memories),
                    "total": total,
                    "memories": [m.to_api_dict() for m in memories],
                }
                return self.make_response(payload=payload)
            except MembankInputError as e:
                return self.make_response(msg=e.message, success=False,
                                          rstatus=400)
            except self._BUSY_ERRORS as e:
                return self._busy_response("/memory/list", e)
            except Exception as e:
                self.log.write("Error in /memory/list (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to list memories.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /memory/get — fetch one memory (read)
        # ------------------------------------------------------------------ #
        @self.server.route("/memory/get", methods=["POST"])
        def endpoint_memory_get():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank = self._acl_read(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=404)

            memory_id = jdata.get("id", None)
            try:
                memory = self.service.dispatch(bank.get_memory, memory_id)
                if memory is None:
                    return self.make_response(rstatus=404)
                return self.make_response(payload={"memory": memory.to_api_dict()})
            except MembankInputError as e:
                return self.make_response(msg=e.message, success=False,
                                          rstatus=400)
            except self._BUSY_ERRORS as e:
                return self._busy_response("/memory/get", e)
            except Exception as e:
                self.log.write("Error in /memory/get (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to get memory.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /memory/add — create a memory (write)
        # ------------------------------------------------------------------ #
        @self.server.route("/memory/add", methods=["POST"])
        def endpoint_memory_add():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank, status = self._acl_write(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=status)

            name = jdata.get("name", None)
            content = jdata.get("content", None)
            tags = jdata.get("tags", [])
            timestamp = jdata.get("timestamp", None)

            try:
                memory = self.service.dispatch(
                    bank.add_memory, name=name, content=content, tags=tags,
                    timestamp=timestamp)
                return self.make_response(payload={"id": memory.id,
                                                   "bank": bank.config.id})
            except MembankInputError as e:
                return self.make_response(msg=e.message, success=False,
                                          rstatus=400)
            except self._BUSY_ERRORS as e:
                return self._busy_response("/memory/add", e)
            except Exception as e:
                self.log.write("Error in /memory/add (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to add memory.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /memory/update — modify a memory (write)
        # ------------------------------------------------------------------ #
        @self.server.route("/memory/update", methods=["POST"])
        def endpoint_memory_update():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank, status = self._acl_write(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=status)

            memory_id = jdata.get("id", None)
            # Only fields explicitly present in the request are changed.
            name = jdata.get("name", None)
            content = jdata.get("content", None)
            tags = jdata.get("tags", None)
            timestamp = jdata.get("timestamp", None)

            try:
                updated = self.service.dispatch(
                    bank.update_memory, memory_id, name=name, content=content,
                    tags=tags, timestamp=timestamp)
                if not updated:
                    return self.make_response(rstatus=404)
                return self.make_response(payload={"id": memory_id,
                                                   "updated": True})
            except MembankInputError as e:
                return self.make_response(msg=e.message, success=False,
                                          rstatus=400)
            except self._BUSY_ERRORS as e:
                return self._busy_response("/memory/update", e)
            except Exception as e:
                self.log.write("Error in /memory/update (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to update memory.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /memory/delete — delete a memory (write)
        # ------------------------------------------------------------------ #
        @self.server.route("/memory/delete", methods=["POST"])
        def endpoint_memory_delete():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank, status = self._acl_write(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=status)

            memory_id = jdata.get("id", None)
            try:
                deleted = self.service.dispatch(bank.delete_memory, memory_id)
                if not deleted:
                    return self.make_response(rstatus=404)
                return self.make_response(payload={"id": memory_id,
                                                   "deleted": True})
            except MembankInputError as e:
                return self.make_response(msg=e.message, success=False,
                                          rstatus=400)
            except self._BUSY_ERRORS as e:
                return self._busy_response("/memory/delete", e)
            except Exception as e:
                self.log.write("Error in /memory/delete (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to delete memory.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /tag/list — list tags in a bank (read)
        # ------------------------------------------------------------------ #
        @self.server.route("/tag/list", methods=["POST"])
        def endpoint_tag_list():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank = self._acl_read(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=404)

            try:
                tags = self.service.dispatch(bank.list_tags)
                return self.make_response(payload={"bank": bank.config.id, "tags": tags})
            except self._BUSY_ERRORS as e:
                return self._busy_response("/tag/list", e)
            except Exception as e:
                self.log.write("Error in /tag/list (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to list tags.",
                                          success=False, rstatus=500)

        # ------------------------------------------------------------------ #
        # POST /bank/rebuild_tags — admin: rebuild the tag index (write)
        # ------------------------------------------------------------------ #
        @self.server.route("/bank/rebuild_tags", methods=["POST"])
        def endpoint_bank_rebuild_tags():
            if not flask.g.user:
                return self.make_response(rstatus=404)
            # Restricted to privilege 0 (admin) users AND write access.
            if flask.g.user.config.privilege != 0:
                return self.make_response(rstatus=404)
            jdata = flask.g.jdata or {}
            bank, status = self._acl_write(self._get_bank_id(jdata))
            if bank is None:
                return self.make_response(rstatus=status)

            try:
                self.service.dispatch(bank.rebuild_tags)
                return self.make_response(payload={"bank": bank.config.id,
                                                   "rebuilt": True})
            except self._BUSY_ERRORS as e:
                return self._busy_response("/bank/rebuild_tags", e)
            except Exception as e:
                self.log.write("Error in /bank/rebuild_tags (bank=%s): %s" %
                               (bank.config.id, str(e)))
                return self.make_response(msg="Failed to rebuild tags.",
                                          success=False, rstatus=500)


    # -------------------------------- NLA ---------------------------------- #
    def describe_nla_endpoint(self, nla_ep):
        """Serializes an NLA endpoint for `/nla/get`, appending a per-request,
        ACL-filtered catalog of the banks accessible to the requesting user so
        the speaker's router LLM has live context on which banks exist.

        `remember` lists WRITABLE banks (matching its write ACL); `recall` lists
        READABLE banks. The catalog is bounded (`NLA_DESC_BANK_CAP`) so the
        router prompt stays small. Other endpoints (and other services) fall
        back to the base `to_json()` behavior.
        """
        data = super().describe_nla_endpoint(nla_ep)
        if nla_ep.name in (NLA_REMEMBER_NAME, NLA_RECALL_NAME):
            username = flask.g.user.config.username
            if nla_ep.name == NLA_REMEMBER_NAME:
                banks = self.service.registry.writable_by(username)
            else:
                banks = self.service.registry.readable_by(username)
            data["description"] = data.get("description", "") + \
                _render_bank_catalog(banks)
        return data

    def init_nla(self):
        """Registers the membank NLA endpoints (STORE + QUERY).

        All natural-language understanding lives here in the NLA layer; the
        HTTP API itself remains explicit ("dumb"). The speaker discovers these
        via `/nla/get` and routes matching utterances to them. Each handler
        performs the same server-side per-bank ACL as the regular endpoints
        (via `flask.g.user`, i.e. the service account that invoked the NLA
        endpoint).
        """
        super().init_nla()
        self.nla_endpoints += [
            NLAEndpoint.from_json({
                "name": NLA_REMEMBER_NAME,
                "description": NLA_REMEMBER_DESC,
            }).set_handler(nla_remember),
            NLAEndpoint.from_json({
                "name": NLA_RECALL_NAME,
                "description": NLA_RECALL_DESC,
            }).set_handler(nla_recall),
        ]


# ================================== Main =================================== #
if __name__ == "__main__":
    cli = ServiceCLI(config=MembankConfig, service=MembankService,
                     oracle=MembankOracle)
    cli.run()
