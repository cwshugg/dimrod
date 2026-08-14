# Implements the /memory (/m) bot command for storing and querying memories via
# the membank service.
#
# This command is PURELY subcommand-driven and deterministic: it maps each
# subcommand 1:1 onto a membank oracle endpoint and never involves the speaker
# or any LLM. Natural-language memory interaction lives ONLY on the
# conversational speaker-NLA path (plain, non-slash messages), not here.
#
# Parsing rule: after the subcommand keyword, the REMAINDER is split on "." into
# fields, with whitespace trimmed around each field AND around "=" in key=value
# pairs (so "lore . kw = mithril . limit = 5" parses the same as
# "lore.kw=mithril.limit=5"). Field 0 is ALWAYS the optional bank: empty or "-"
# means "use the chat's configured default bank"; anything else is fuzzy-matched
# CLIENT-SIDE against the caller's accessible banks (exact id -> exact name ->
# unique name substring; ambiguous references list the candidate banks). See
# `_match_bank`, which mirrors the server's `MemoryBankRegistry.resolve_ref`.
# Fields 1+ are the parameters for the subcommand.
#
# LIMITATION: because "." is the field delimiter and there is no escape
# mechanism, a field value cannot contain a literal "." — e.g. `/m add` content
# with a period is split into extra fields (surfacing as tags/timestamp) and can
# produce a confusing "Bad timestamp" error rather than storing the note.
#
# All replies are rendered as Telegram HTML; every piece of dynamic text is
# escaped here in the command source. We deliberately do NOT touch telegram's
# shared `sanitize_message_text`.

# Imports
import os
import sys
import html
from datetime import datetime, timezone

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession

# The sentinel field value that means "use the chat's configured default bank".
BANK_DEFAULT_SENTINEL = "-"

# The membank list endpoint clamps page size to this hard cap; we mirror it in
# help text and note when a page is truncated.
SEARCH_LIMIT_CAP = 500

# The maximum length of a content snippet shown in compact search results.
SEARCH_SNIPPET_LEN = 80


# ================================= Helpers ================================= #
def _esc(text) -> str:
    """Escapes text for safe use inside a Telegram HTML message."""
    if text is None:
        return ""
    return html.escape(str(text))


def _remainder_after_command(message, args: list) -> str:
    """Returns the raw text after the leading command token.

    Uses the raw message text (preserving spacing/casing) rather than the
    pre-split args, so field contents are forwarded faithfully. Strips the
    leading command token (e.g. "/memory" or "/m@BotName").
    """
    text = message.text if message.text is not None else ""
    text = text.strip()
    if len(args) > 0:
        parts = text.split(None, 1)
        if len(parts) > 1:
            return parts[1].strip()
    return ""


def _split_subcommand(remainder: str):
    """Splits the command remainder into ``(subcommand, rest)``.

    The subcommand is the first whitespace-delimited word (lowercased); ``rest``
    is everything after it (the field string), stripped. Returns ``("", "")``
    when the remainder is empty.
    """
    remainder = remainder.strip()
    if len(remainder) == 0:
        return "", ""
    parts = remainder.split(None, 1)
    subcommand = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return subcommand, rest


def _parse_fields(rest: str) -> list:
    """Splits the field string on ``.`` and trims whitespace around each field.

    ``"a . b . c"`` and ``"a.b.c"`` parse identically. An empty field string
    yields ``[""]`` so callers can treat field 0 (the bank) uniformly.
    """
    return [field.strip() for field in rest.split(".")]


def _parse_kv(field: str):
    """Parses a single ``key=value`` field, trimming whitespace around ``=``.

    Returns ``(key_lower, value)``. If no ``=`` is present, returns
    ``(field_lower, None)``.
    """
    if "=" not in field:
        return field.strip().lower(), None
    key, value = field.split("=", 1)
    return key.strip().lower(), value.strip()


def _to_epoch(text: str):
    """Converts a user-supplied date/epoch string to integer epoch seconds.

    Accepts a bare integer epoch, ``YYYY-MM-DD``, or ``YYYY-MM-DD HH:MM:SS``
    (interpreted as UTC). Returns ``(epoch_int, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    text = text.strip()
    if len(text) == 0:
        return None, "empty date/time value"

    # Bare epoch seconds (optionally negative).
    if text.lstrip("-").isdigit():
        try:
            return int(text), None
        except ValueError:
            return None, "invalid epoch value: %s" % text

    date_formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
    for fmt in date_formats:
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp()), None
        except ValueError:
            continue
    return None, ("unrecognized date/time '%s' (use epoch seconds, "
                  "YYYY-MM-DD, or YYYY-MM-DD HH:MM:SS)" % text)


def _format_timestamp(ts) -> str:
    """Formats an epoch-seconds timestamp as a readable UTC string."""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError):
        return str(ts)


# --------------------------- session + bank helpers ------------------------ #
def _get_membank_session(service, message):
    """Creates and authenticates an `OracleSession` with the membank service.

    Returns the session on success, or None on failure (after sending an
    error message to the user).
    """
    if not hasattr(service.config, "membank") or \
            service.config.membank is None:
        service.send_message(message.chat.id,
                             "Memory bank is not configured for this bot.")
        return None

    session = OracleSession(service.config.membank)
    try:
        r = session.login()
    except Exception:
        service.send_message(message.chat.id,
                             "Sorry, I couldn't reach my memory. "
                             "It might be offline.")
        return None

    if r.status_code != 200 or not session.get_response_success(r):
        service.send_message(message.chat.id,
                             "Sorry, I couldn't authenticate with my memory.")
        return None
    return session


def _normalize_ref(ref) -> str:
    """Normalizes a bank reference for case/whitespace-insensitive matching.

    Mirrors the membank server's `MemoryBankRegistry._normalize_ref`: trims,
    lowers, and collapses internal whitespace to single spaces. Returns "" for
    None.
    """
    if ref is None:
        return ""
    return " ".join(str(ref).strip().lower().split())


def _list_accessible_banks(session):
    """Fetches the caller's accessible banks via the membank ``/bank/list``
    endpoint (the same call `_cmd_banks` uses).

    Returns ``(banks, None)`` on success (a list of bank dicts, each with
    ``id``, ``name``, ``can_write``, ...) or ``(None, error)`` with a friendly,
    HTML-safe message when the lookup could not be performed.
    """
    try:
        r = session.post("/bank/list", payload={})
    except Exception:
        return None, ("Sorry, I couldn't reach my memory to look up your "
                      "banks. It might be offline.")
    if not session.get_response_success(r):
        return None, ("Sorry, I couldn't look up your memory banks right now; "
                      "please try again shortly.")
    data = session.get_response_json(r)
    banks = data.get("banks", []) if isinstance(data, dict) else []
    return banks, None


def _match_bank(raw: str, banks: list):
    """Matches a typed bank reference against the caller's accessible banks.

    Mirrors the server-side `MemoryBankRegistry.resolve_ref` semantics, applied
    CLIENT-SIDE. All comparisons are case-insensitive with trimmed/collapsed
    whitespace, in priority order:

      1. exact ``id`` match
      2. exact ``name`` match
      3. UNIQUE substring match: exactly one accessible bank whose normalized
         ``name`` contains, or is contained by, the normalized ref
         (bidirectional containment against NAMES only — identical to the
         server's `resolve_ref` step 3)

    Returns ``(bank_id, None)`` on a unique resolution, or ``(None, error)``
    with a friendly, HTML-escaped message when the reference is ambiguous
    (>1 substring match) or matches no accessible bank.
    """
    norm = _normalize_ref(raw)
    banks = banks or []

    # 1. Exact id.
    for bank in banks:
        if _normalize_ref(bank.get("id")) == norm:
            return bank.get("id"), None

    # 2. Exact name.
    for bank in banks:
        if _normalize_ref(bank.get("name")) == norm:
            return bank.get("id"), None

    # 3. Unique substring: bidirectional containment against NAMES only, to
    #    mirror the server's `resolve_ref` step 3 exactly (so `/m` and the
    #    conversational NLA path resolve any given reference identically).
    matches = []
    for bank in banks:
        norm_name = _normalize_ref(bank.get("name"))
        if len(norm_name) == 0:
            continue
        if norm in norm_name or norm_name in norm:
            matches.append(bank)

    if len(matches) == 1:
        return matches[0].get("id"), None

    if len(matches) > 1:
        lines = ["Bank <code>%s</code> is ambiguous — it matches several "
                 "banks:" % _esc(raw)]
        for bank in matches:
            lines.append("• <code>%s</code> — %s" % (
                _esc(bank.get("id")),
                _esc(bank.get("name", bank.get("id"))),
            ))
        lines.append("Please use a more specific name or the exact id.")
        return None, "\n".join(lines)

    return None, ("I couldn't find a memory bank matching <code>%s</code> "
                  "that you can access. Use <code>/m banks</code> to see the "
                  "banks available to you." % _esc(raw))


def _resolve_bank(service, message, fields: list, session=None):
    """Resolves the target bank id from field 0.

    Field 0 is the optional bank: empty or ``-`` (`BANK_DEFAULT_SENTINEL`) means
    "use the chat's configured default bank" (via `get_chat_memory_bank`); that
    default is already a real id and is used verbatim. Any other value is
    fuzzy-matched, CLIENT-SIDE, against the banks THIS caller can access (fetched
    via ``/bank/list``) using `_match_bank`.

    An authenticated membank `session` may be passed in to avoid re-authing; if
    omitted, one is built via `_get_membank_session` (which reports its own
    error and returns None on failure).

    Returns ``(bank_id, None)`` on success. On failure returns ``(None, error)``
    where ``error`` is an HTML-safe user message, or ``(None, None)`` when the
    failure has already been reported to the user (e.g. auth failed).
    """
    raw = fields[0].strip() if len(fields) > 0 else ""
    if raw == "" or raw == BANK_DEFAULT_SENTINEL:
        default = service.get_chat_memory_bank(message.chat.id)
        if default is None or (isinstance(default, str) and len(default) == 0):
            return None, ("No memory bank was given and this chat has no "
                          "default bank configured. Specify a bank id as the "
                          "first field, or ask an admin to set this chat's "
                          "<code>memory_bank</code>.")
        return default, None

    # Non-empty field 0: fuzzy-resolve against the caller's accessible banks.
    if session is None:
        session = _get_membank_session(service, message)
        if session is None:
            # _get_membank_session already sent an error message to the user.
            return None, None

    banks, list_err = _list_accessible_banks(session)
    if list_err is not None:
        return None, list_err
    return _match_bank(raw, banks)


def _send_endpoint_error(service, message, session, r, context: str) -> bool:
    """Sends a friendly, escaped-HTML message for a failed endpoint response.

    Maps membank's server-side ACL / validation statuses onto readable text.
    Always returns False so callers can ``return`` it directly.
    """
    status = r.status_code
    try:
        detail = session.get_response_message(r)
    except Exception:
        detail = None

    if status == 400:
        msg = "That request was rejected: %s" % \
            _esc(detail or "invalid input.")
    elif status == 403:
        msg = ("You don't have permission to %s in that bank "
               "(it may be read-only for you)." % _esc(context))
    elif status == 404:
        msg = ("I couldn't find that (unknown bank/memory, or you don't have access).")
    elif status == 503:
        msg = "My memory is busy right now; please try again shortly."
    else:
        msg = "Sorry, I couldn't %s. (%s)" % \
            (_esc(context), _esc(detail or ("HTTP %s" % status)))
    service.send_message(message.chat.id, msg, parse_mode="HTML")
    return False


# =============================== Subcommands =============================== #
def _cmd_banks(service, message, fields: list) -> bool:
    """`/m banks` -> POST /bank/list. Lists banks the caller may access.

    The bank field is ignored for this subcommand.
    """
    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/bank/list", payload={})
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r,
                                    "list banks")

    data = session.get_response_json(r)
    banks = data.get("banks", []) if isinstance(data, dict) else []
    if len(banks) == 0:
        service.send_message(message.chat.id,
                             "You don't have access to any memory banks.")
        return True

    lines = ["<b>Memory banks:</b>"]
    for bank in banks:
        access = "rw" if bank.get("can_write") else "ro"
        lines.append("• <code>%s</code> — %s (%s, %s memories)" % (
            _esc(bank.get("id")),
            _esc(bank.get("name", bank.get("id"))),
            access,
            _esc(bank.get("memory_count", 0)),
        ))
    service.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")
    return True


def _cmd_tags(service, message, fields: list) -> bool:
    """`/m tags [bank]` -> POST /tag/list. Lists tags and their counts."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/tag/list", payload={"bank": bank_id})
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r, "list tags")

    data = session.get_response_json(r)
    tags = data.get("tags", []) if isinstance(data, dict) else []
    if len(tags) == 0:
        service.send_message(message.chat.id,
                             "Bank <code>%s</code> has no tags yet." %
                             _esc(bank_id), parse_mode="HTML")
        return True

    lines = ["<b>Tags in <code>%s</code>:</b>" % _esc(bank_id)]
    for tag in tags:
        lines.append("• <code>%s</code> (%s)" % (
            _esc(tag.get("tag")), _esc(tag.get("count", 0))))
    service.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")
    return True


def _cmd_add(service, message, fields: list) -> bool:
    """`/m add [bank].name.content[.tags][.timestamp]` -> POST /memory/add."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    params = fields[1:]
    name = params[0] if len(params) > 0 else ""
    content = params[1] if len(params) > 1 else ""
    if len(name) == 0 or len(content) == 0:
        _usage(service, message, "add")
        return False

    payload = {"bank": bank_id, "name": name, "content": content}

    # Optional tags (comma-separated) in field 3. The service validates them.
    if len(params) > 2 and len(params[2]) > 0:
        payload["tags"] = [t.strip() for t in params[2].split(",")
                           if len(t.strip()) > 0]

    # Optional timestamp (epoch or date) in field 4.
    if len(params) > 3 and len(params[3]) > 0:
        epoch, ts_err = _to_epoch(params[3])
        if ts_err is not None:
            service.send_message(message.chat.id,
                                 "Bad timestamp: %s" % _esc(ts_err),
                                 parse_mode="HTML")
            return False
        payload["timestamp"] = epoch

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/memory/add", payload=payload)
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r, "add memory")

    data = session.get_response_json(r)
    mem_id = data.get("id") if isinstance(data, dict) else None
    service.send_message(message.chat.id,
                         "Saved to <code>%s</code> as <code>%s</code>." % (
                             _esc(bank_id), _esc(mem_id)),
                         parse_mode="HTML")
    return True


def _cmd_get(service, message, fields: list) -> bool:
    """`/m get [bank].id` -> POST /memory/get. Renders the full memory."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    mem_id = fields[1] if len(fields) > 1 else ""
    if len(mem_id) == 0:
        _usage(service, message, "get")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/memory/get", payload={"bank": bank_id, "id": mem_id})
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r, "get memory")

    data = session.get_response_json(r)
    memory = data.get("memory") if isinstance(data, dict) else None
    if memory is None:
        service.send_message(message.chat.id, "That memory was not found.")
        return True

    service.send_message(message.chat.id, _render_memory(memory, bank_id),
                         parse_mode="HTML")
    return True


def _cmd_search(service, message, fields: list) -> bool:
    """`/m search [bank].k=v...` -> POST /memory/list. Compact result list."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    payload, perr = _build_search_payload(bank_id, fields[1:])
    if perr is not None:
        service.send_message(message.chat.id, perr, parse_mode="HTML")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/memory/list", payload=payload)
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r,
                                    "search memories")

    data = session.get_response_json(r)
    service.send_message(message.chat.id,
                         _render_search(data, bank_id, payload),
                         parse_mode="HTML")
    return True


def _cmd_edit(service, message, fields: list) -> bool:
    """`/m edit [bank].id.k=v...` -> POST /memory/update. Partial updates."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    mem_id = fields[1] if len(fields) > 1 else ""
    if len(mem_id) == 0:
        _usage(service, message, "edit")
        return False

    payload = {"bank": bank_id, "id": mem_id}
    allowed = ("name", "content", "tags")
    saw_update = False
    for field in fields[2:]:
        if len(field) == 0:
            continue
        key, value = _parse_kv(field)
        if key not in allowed or value is None:
            service.send_message(message.chat.id,
                                 "Unknown edit field <code>%s</code>. "
                                 "Use name=, content=, or tags=." % _esc(key),
                                 parse_mode="HTML")
            return False
        if key == "tags":
            payload["tags"] = [t.strip() for t in value.split(",")
                               if len(t.strip()) > 0]
        else:
            payload[key] = value
        saw_update = True

    if not saw_update:
        _usage(service, message, "edit")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/memory/update", payload=payload)
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r,
                                    "edit memory")

    service.send_message(message.chat.id,
                         "Updated <code>%s</code> in <code>%s</code>." % (
                             _esc(mem_id), _esc(bank_id)),
                         parse_mode="HTML")
    return True


def _cmd_del(service, message, fields: list) -> bool:
    """`/m del [bank].id` -> POST /memory/delete."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    mem_id = fields[1] if len(fields) > 1 else ""
    if len(mem_id) == 0:
        _usage(service, message, "del")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/memory/delete", payload={"bank": bank_id, "id": mem_id})
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r,
                                    "delete memory")

    service.send_message(message.chat.id,
                         "Deleted <code>%s</code> from <code>%s</code>." % (
                             _esc(mem_id), _esc(bank_id)),
                         parse_mode="HTML")
    return True


def _cmd_rebuild(service, message, fields: list) -> bool:
    """`/m rebuild [bank]` -> POST /bank/rebuild_tags (admin)."""
    bank_id, err = _resolve_bank(service, message, fields)
    if bank_id is None:
        if err is not None:
            service.send_message(message.chat.id, err, parse_mode="HTML")
        return False

    session = _get_membank_session(service, message)
    if session is None:
        return False

    r = session.post("/bank/rebuild_tags", payload={"bank": bank_id})
    if not session.get_response_success(r):
        return _send_endpoint_error(service, message, session, r,
                                    "rebuild tags")

    service.send_message(message.chat.id,
                         "Rebuilt the tag index for <code>%s</code>." %
                         _esc(bank_id), parse_mode="HTML")
    return True


# =============================== Rendering ================================ #
def _memory_snippet(content) -> str:
    """Returns a single-line, length-capped snippet of a memory's content."""
    text = " ".join(str(content or "").split())
    if len(text) > SEARCH_SNIPPET_LEN:
        text = text[:SEARCH_SNIPPET_LEN].rstrip() + "…"
    return text


def _render_memory(memory: dict, bank_id: str) -> str:
    """Renders a full memory (name, id, timestamp, tags, content) as HTML."""
    tags = memory.get("tags", []) or []
    tag_str = ", ".join(_esc(t) for t in tags) if len(tags) > 0 else "(none)"
    lines = [
        "<b>%s</b>" % _esc(memory.get("name", "(untitled)")),
        "<i>id:</i> <code>%s</code>" % _esc(memory.get("id")),
        "<i>bank:</i> <code>%s</code>" % _esc(bank_id),
        "<i>when:</i> %s" % _esc(_format_timestamp(memory.get("timestamp"))),
        "<i>tags:</i> %s" % tag_str,
        "",
        _esc(memory.get("content", "")),
    ]
    return "\n".join(lines)


def _render_search(data, bank_id: str, payload: dict) -> str:
    """Renders a compact HTML list of search results honoring count/total."""
    if not isinstance(data, dict):
        return "No results."
    memories = data.get("memories", []) or []
    count = data.get("count", len(memories))
    total = data.get("total", count)

    if count == 0:
        return "No memories in <code>%s</code> matched." % _esc(bank_id)

    header = "<b>%s of %s match(es) in <code>%s</code>:</b>" % (
        _esc(count), _esc(total), _esc(bank_id))
    lines = [header]
    for memory in memories:
        tags = memory.get("tags", []) or []
        tag_str = " ".join("#" + _esc(t) for t in tags)
        title = "<b>%s</b>" % _esc(memory.get("name", "(untitled)"))
        ts = memory.get("timestamp")
        if ts is not None:
            title += " — <i>%s</i>" % _esc(_format_timestamp(ts))
        line = "• %s\n    • ID: <code>%s</code>" % (
            title,
            _esc(memory.get("id"))
        )
        if len(tag_str) > 0:
            line += "\n    • Tags: %s" % tag_str
        snippet = _memory_snippet(memory.get("content"))
        if len(snippet) > 0:
            line += "\n    • Content (snippet): <i>%s</i>" % _esc(snippet)
        lines.append("\n" + line)

    # Note truncation when the returned page is capped by the limit.
    limit = payload.get("limit")
    if isinstance(limit, int) and count >= limit and total > count:
        lines.append("<i>…more results were truncated by limit=%s; refine "
                     "your query or raise the limit.</i>" % _esc(limit))
    elif total > count:
        lines.append("<i>…%s more not shown; use offset= to page.</i>" %
                     _esc(total - count))
    return "\n".join(lines)


# ============================ Search payload ============================== #
def _build_search_payload(bank_id: str, kv_fields: list):
    """Builds the /memory/list payload from ``key=value`` search fields.

    Returns ``(payload, None)`` on success or ``(None, error)`` (an escaped-HTML
    error string) when a field is invalid. Recognized keys:
    ``kw``, ``tags``, ``mode``, ``from``, ``to``, ``limit``, ``offset``,
    ``order``.
    """
    payload = {"bank": bank_id}
    filters = {}
    time_range = {}

    for field in kv_fields:
        if len(field) == 0:
            continue
        key, value = _parse_kv(field)
        if value is None:
            return None, ("Search filters must be <code>key=value</code>; got "
                          "<code>%s</code>." % _esc(key))

        if key == "kw":
            filters["keyword"] = value
        elif key == "tags":
            filters["tags"] = [t.strip() for t in value.split(",")
                               if len(t.strip()) > 0]
        elif key == "mode":
            mode = value.lower()
            if mode not in ("any", "all"):
                return None, "mode= must be 'any' or 'all'."
            filters["tag_mode"] = mode
        elif key in ("from", "to"):
            epoch, err = _to_epoch(value)
            if err is not None:
                return None, "Bad %s date: %s" % (_esc(key), _esc(err))
            time_range["start" if key == "from" else "end"] = epoch
        elif key == "limit":
            ival, err = _parse_int(value)
            if err is not None:
                return None, "limit= must be an integer."
            payload["limit"] = min(ival, SEARCH_LIMIT_CAP)
        elif key == "offset":
            ival, err = _parse_int(value)
            if err is not None:
                return None, "offset= must be an integer."
            payload["offset"] = max(ival, 0)
        elif key == "order":
            order = value.lower()
            if order not in ("asc", "desc"):
                return None, "order= must be 'asc' or 'desc'."
            payload["order"] = order
        else:
            return None, ("Unknown search filter <code>%s</code>. Use kw=, "
                          "tags=, mode=, from=, to=, limit=, offset=, order=." %
                          _esc(key))

    if len(time_range) > 0:
        filters["time_range"] = time_range
    if len(filters) > 0:
        payload["filters"] = filters
    return payload, None


def _parse_int(value: str):
    """Parses an integer, returning ``(int, None)`` or ``(None, error)``."""
    try:
        return int(value.strip()), None
    except (ValueError, AttributeError):
        return None, "not an integer"


# ================================= Help =================================== #
_USAGE = {
    "banks": "/m banks",
    "tags": "/m tags [bank]",
    "add": "/m add [bank].name.content[.tags][.timestamp]",
    "get": "/m get [bank].id",
    "search": "/m search [bank].kw=….tags=a,b.mode=any|all."
              "from=YYYY-MM-DD.to=YYYY-MM-DD.limit=N.offset=N.order=asc|desc",
    "edit": "/m edit [bank].id.name=….content=….tags=a,b",
    "del": "/m del [bank].id",
    "rebuild": "/m rebuild [bank]",
}


def _usage(service, message, subcommand: str) -> None:
    """Sends a per-subcommand usage error."""
    usage = _USAGE.get(subcommand)
    msg = "Usage: <code>%s</code>" % _esc(usage)
    service.send_message(message.chat.id, msg, parse_mode="HTML")


def _memory_help(service, message) -> None:
    """Sends usage help listing every subcommand plus examples."""
    bank_id = service.get_chat_memory_bank(message.chat.id)

    lines = [
        "<b>/memory</b> (alias <b>/m</b>) — structured memory commands.",
        "",
        "The first field is the <b>bank</b>: leave it empty or use "
        "<code>-</code> for this chat's default bank. Otherwise it is "
        "fuzzy-matched against the banks you can access: exact id, then exact "
        "name, then a unique name substring; if a reference matches more than "
        "one bank the reply lists the candidates so you can be more specific.",
        "Fields are separated by <code>.</code> and whitespace is ignored "
        "(<code>a . b</code> == <code>a.b</code>).",
        "Because <code>.</code> is the field separator, a field value cannot "
        "contain a literal <code>.</code> — e.g. an <code>add</code> content "
        "with a period is split into extra fields and may report a confusing "
        "\"Bad timestamp\". Omit periods from names/content.",
        "",
        "<b>Subcommands:</b>",
        "• <code>%s</code> — list banks you can access" % _esc(_USAGE["banks"]),
        "• <code>%s</code> — list tags + counts" % _esc(_USAGE["tags"]),
        "• <code>%s</code> — add a memory" % _esc(_USAGE["add"]),
        "• <code>%s</code> — show one memory" % _esc(_USAGE["get"]),
        "• <code>%s</code> — search memories" % _esc(_USAGE["search"]),
        "• <code>%s</code> — edit fields" % _esc(_USAGE["edit"]),
        "• <code>%s</code> — delete a memory" % _esc(_USAGE["del"]),
        "• <code>%s</code> — rebuild tag index (admin)" %
        _esc(_USAGE["rebuild"]),
        "",
        "<b>Search filters:</b> <code>kw=</code> keyword, "
        "<code>tags=a,b</code>, <code>mode=any|all</code>, "
        "<code>from=</code>/<code>to=</code> dates, "
        "<code>limit=</code> (≤%d), <code>offset=</code>, "
        "<code>order=asc|desc</code>." % SEARCH_LIMIT_CAP,
        "Dates accept epoch seconds, <code>YYYY-MM-DD</code>, or "
        "<code>YYYY-MM-DD HH:MM:SS</code> (UTC).",
        "",
        "<b>Examples:</b>",
        "<code>/m search lore.kw=mithril.limit=5</code>",
        "<code>/m add .Parking.Section G7 near the elevator.parking</code>",
        "<code>/m get lore.abc123</code>",
        "<code>/m edit lore.abc123.tags=mithril,mines</code>",
    ]

    if bank_id is not None:
        lines.append("")
        lines.append("This chat's default bank: <code>%s</code>" %
                     _esc(bank_id))
    else:
        lines.append("")
        lines.append("<i>This chat has no default bank; name one as the first "
                     "field.</i>")

    service.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


# =============================== Dispatch ================================= #
# Maps each subcommand keyword onto its handler.
_SUBCOMMANDS = {
    "banks": _cmd_banks,
    "tags": _cmd_tags,
    "add": _cmd_add,
    "get": _cmd_get,
    "search": _cmd_search,
    "edit": _cmd_edit,
    "del": _cmd_del,
    "rebuild": _cmd_rebuild,
}


# =================================== Main =================================== #
def command_memory(service, message, args: list):
    """Main handler for the /memory (/m) command.

    Purely subcommand-driven: dispatches to the handler that maps 1:1 onto a
    membank oracle endpoint. Never involves the speaker or any LLM.
    """
    remainder = _remainder_after_command(message, args)
    subcommand, rest = _split_subcommand(remainder)

    # No subcommand, or an explicit help request -> show help.
    if subcommand == "" or subcommand == "help":
        _memory_help(service, message)
        return True

    handler = _SUBCOMMANDS.get(subcommand)
    if handler is None:
        service.send_message(message.chat.id,
                             "Unknown subcommand <code>%s</code>." %
                             _esc(subcommand), parse_mode="HTML")
        _memory_help(service, message)
        return False

    fields = _parse_fields(rest)
    return handler(service, message, fields)
