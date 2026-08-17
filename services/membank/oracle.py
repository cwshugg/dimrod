# Module that defines a generic client wrapper around `lib.oracle.OracleSession`
# for the membank service. It exposes one method per membank HTTP endpoint so
# that any service (or script) can talk to membank without hand-rolling
# `.post("/memory/add", {...})` calls everywhere.
#
# This is a GENERIC client: there is no event-logging-specific logic or naming
# here. Higher-level helpers (e.g. logging events to the `events` bank) are a
# separate concern and live elsewhere.

# Imports
import os
import sys
from typing import Any, Dict, List, Optional

import requests

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession, OracleSessionConfig


# ========================= Membank Oracle Errors =========================== #
class MembankOracleSessionError(Exception):
    """Raised when a membank oracle request fails.

    A request is considered failed when the server reports `success == False`,
    responds with a non-2xx status, or when a transport/JSON-decoding error
    occurs. The exception message carries the server-provided message (when
    available) or the underlying transport error text.

    Note: this is a generic client wrapper, so it raises on error. Callers that
    perform best-effort work (such as fire-and-forget event logging) should
    wrap their calls in a try/except and handle this exception themselves.
    """
    pass


# ========================= Membank Oracle Session ========================== #
class MembankOracleSession(OracleSession):
    """A typed, reusable client for the membank service.

    This subclass of `lib.oracle.OracleSession` provides one helper method per
    exposed membank endpoint. Each method:
      * Lazily logs in on the first request (and re-authenticates once on a 401).
      * Sends a POST to the appropriate membank endpoint.
      * Returns the parsed response payload (a dict) on success.
      * Raises `MembankOracleSessionError` on failure.

    All membank endpoints accept an optional `bank_id` in their payload. When
    `bank_id` is None it is omitted from the request, and membank falls back to
    its configured `default_bank`. Note that the on-the-wire JSON key remains
    `"bank"`; only the Python parameter name is `bank_id`.
    """
    def __init__(self, config: OracleSessionConfig) -> None:
        """Constructor. Takes in an `OracleSessionConfig` and initializes the
        underlying `OracleSession`, plus internal auth state.
        """
        super().__init__(config)
        self._authenticated = False

    # ----------------------------- Internals ------------------------------- #
    def _request(self, endpoint: str,
                 payload: Optional[Dict[str, Any]] = None
                 ) -> "requests.Response":
        """Sends a POST request to `endpoint`, handling lazy login and a single
        401 re-authentication retry.

        Behavior:
          1. If not yet authenticated, log in first (HTTP 200 marks success).
          2. POST the payload to `endpoint` (leading-slash endpoint strings are
             used to match existing membank caller conventions).
          3. If the response status is 401 (session expired), re-login once and
             retry the POST exactly once.
          4. Return the final `requests.Response`.
        """
        # Lazy login: authenticate before the first request.
        if not self._authenticated:
            login_response = self.login()
            if login_response.status_code == 200:
                self._authenticated = True

        # Send the request.
        response = super().post(endpoint, payload)

        # If the session has expired, re-authenticate once and retry.
        if response.status_code == 401:
            login_response = self.login()
            if login_response.status_code == 200:
                self._authenticated = True
            response = super().post(endpoint, payload)

        return response

    def _handle(self, response: "requests.Response") -> Dict[str, Any]:
        """Interprets a membank `requests.Response` and returns its payload.

        On success (`get_response_success(...) == True`), returns the parsed
        JSON payload dict (`get_response_json(...)`). On any failure (success
        False, non-2xx status, or a transport/JSON-decoding error), raises
        `MembankOracleSessionError` carrying the server message (or transport
        error text).
        """
        try:
            success = OracleSession.get_response_success(response)
        except Exception as e:
            # Transport error, non-JSON body, or missing 'success' field.
            raise MembankOracleSessionError(
                "membank request failed: %s" % str(e))

        if not success:
            # Try to surface the server-provided error message.
            try:
                message = OracleSession.get_response_message(response)
            except Exception:
                message = "unknown error"
            raise MembankOracleSessionError(message)

        try:
            return OracleSession.get_response_json(response)
        except Exception as e:
            raise MembankOracleSessionError(
                "membank request succeeded but returned an unparseable "
                "payload: %s" % str(e))

    def _call(self, endpoint: str,
              payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience wrapper: sends a request and returns its parsed payload,
        raising `MembankOracleSessionError` on failure.
        """
        response = self._request(endpoint, payload)
        return self._handle(response)

    # --------------------------- Bank Endpoints ---------------------------- #
    def bank_list(self) -> Dict[str, Any]:
        """Lists all banks readable by the authenticated user.

        POST `/bank/list` (no body).

        Returns:
            dict: `{"banks": [...]}` where each entry describes a bank
            (`id`, `name`, `can_write`, `memory_count`).
        """
        return self._call("/bank/list")

    def bank_rebuild_tags(self, bank_id: Optional[str] = None
                          ) -> Dict[str, Any]:
        """Rebuilds the tag index for a bank (admin / privilege-0 only).

        POST `/bank/rebuild_tags` with an optional `{"bank"}`.

        Args:
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"bank", "rebuilt"}`.
        """
        payload: Dict[str, Any] = {}
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/bank/rebuild_tags", payload)

    # -------------------------- Memory Endpoints --------------------------- #
    def memory_list(self, bank_id: Optional[str] = None,
                    filters: Optional[Dict[str, Any]] = None,
                    limit: Optional[int] = None, offset: int = 0,
                    order: str = "desc") -> Dict[str, Any]:
        """Lists memories in a bank with optional filtering and pagination.

        POST `/memory/list`.

        Args:
            bank_id: Optional bank id. When None, membank uses its default bank.
            filters: Optional filter specification (included only when not None).
            limit: Optional maximum number of results (included only when not
                None).
            offset: Result offset for pagination (default 0).
            order: Sort order, "desc" or "asc" (default "desc").

        Returns:
            dict: `{"bank", "count", "total", "memories"}`.
        """
        payload: Dict[str, Any] = {
            "offset": offset,
            "order": order,
        }
        if bank_id is not None:
            payload["bank"] = bank_id
        if filters is not None:
            payload["filters"] = filters
        if limit is not None:
            payload["limit"] = limit
        return self._call("/memory/list", payload)

    def memory_get(self, memory_id: str, bank_id: Optional[str] = None
                   ) -> Dict[str, Any]:
        """Fetches a single memory by id.

        POST `/memory/get` with `{"id", "bank"?}`.

        Args:
            memory_id: The id of the memory to fetch.
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"memory": {...}}`.
        """
        payload: Dict[str, Any] = {"id": memory_id}
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/memory/get", payload)

    def memory_add(self, name: str, content: str,
                   tags: Optional[List[str]] = None,
                   timestamp: Optional[int] = None,
                   bank_id: Optional[str] = None) -> Dict[str, Any]:
        """Creates a new memory in a bank.

        POST `/memory/add` with `{"name", "content", "tags", "timestamp"?,
        "bank"?}`.

        Args:
            name: The memory's name.
            content: The memory's content.
            tags: Optional list of tags (defaults to `[]` when None).
            timestamp: Optional timestamp (included only when not None).
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"id", "bank"}`.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "content": content,
            "tags": tags if tags is not None else [],
        }
        if timestamp is not None:
            payload["timestamp"] = timestamp
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/memory/add", payload)

    def memory_update(self, memory_id: str, name: Optional[str] = None,
                      content: Optional[str] = None,
                      tags: Optional[List[str]] = None,
                      timestamp: Optional[int] = None,
                      bank_id: Optional[str] = None) -> Dict[str, Any]:
        """Updates an existing memory. Only provided fields are changed.

        POST `/memory/update` with `{"id"}` plus only the provided fields
        (`name`, `content`, `tags`, `timestamp`, `bank`).

        Args:
            memory_id: The id of the memory to update.
            name: Optional new name (included only when not None).
            content: Optional new content (included only when not None).
            tags: Optional new tag list (included only when not None).
            timestamp: Optional new timestamp (included only when not None).
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"id", "updated"}`.
        """
        payload: Dict[str, Any] = {"id": memory_id}
        if name is not None:
            payload["name"] = name
        if content is not None:
            payload["content"] = content
        if tags is not None:
            payload["tags"] = tags
        if timestamp is not None:
            payload["timestamp"] = timestamp
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/memory/update", payload)

    def memory_delete(self, memory_id: str, bank_id: Optional[str] = None
                      ) -> Dict[str, Any]:
        """Deletes a memory by id.

        POST `/memory/delete` with `{"id", "bank"?}`.

        Args:
            memory_id: The id of the memory to delete.
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"id", "deleted"}`.
        """
        payload: Dict[str, Any] = {"id": memory_id}
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/memory/delete", payload)

    # ---------------------------- Tag Endpoints ---------------------------- #
    def tag_list(self, bank_id: Optional[str] = None) -> Dict[str, Any]:
        """Lists all tags used within a bank.

        POST `/tag/list` with an optional `{"bank"}`.

        Args:
            bank_id: Optional bank id. When None, membank uses its default bank.

        Returns:
            dict: `{"bank", "tags"}`.
        """
        payload: Dict[str, Any] = {}
        if bank_id is not None:
            payload["bank"] = bank_id
        return self._call("/tag/list", payload)
