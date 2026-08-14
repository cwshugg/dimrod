#!/usr/bin/python3
# The membank NLA (Natural Language Actions) layer.
#
# This module is the ONLY place in the membank service where LLM logic lives.
# The HTTP API (see `membank.py`) is deliberately "dumb"/explicit: it accepts
# pre-computed `name`/`tags`/`content` and structured filters. This module turns
# free-form natural language into those explicit values, then drives the same
# ACL-guarded bank operations the HTTP endpoints use.
#
# Two intents are supported, exposed as two NLA endpoints so the speaker's LLM
# router can decide store-vs-query by picking the endpoint:
#
#   * STORE  (`nla_remember`) — "Remember this: ...", "note that ..." — the LLM
#     extracts an explicit name + tags (+ optional bank) and adds the memory.
#   * QUERY  (`nla_recall`)   — "what did I tell you last month about ...?" —
#     the LLM emits structured filters (tags / time-range / keyword) and the
#     matching memories are formatted into an answer.
#
# See the architecture report `dba181c2549c113f` (§7) for the full design.
#
#   Connor Shugg

# Imports
import os
import sys
import json
import html
import time
from datetime import datetime

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Enable import from this service's directory
sdir = os.path.dirname(os.path.realpath(__file__))
if sdir not in sys.path:
    sys.path.append(sdir)

# Local library imports
from lib.nla import NLAEndpointInvokeParameters, NLAResult

# Local service imports
from models import MembankInputError


# ================================ Constants ================================ #
# NLA endpoint identifiers + router trigger descriptions.
NLA_REMEMBER_NAME = "remember"
NLA_REMEMBER_DESC = (
    "Store a new memory, note, or idea for later recall. "
    "Use for phrases like \"remember this: ...\", \"note that ...\", "
    "\"here's an idea: ...\", \"save this memory ...\", "
    "\"don't let me forget ...\". The message contains something the user "
    "wants persisted so it can be recalled later."
)

NLA_RECALL_NAME = "recall"
NLA_RECALL_DESC = (
    "Recall previously stored memories, notes, or ideas. "
    "Use for questions like \"what did I tell you last month about ...?\", "
    "\"what did I save about...?\", "
    "\"what was the number for ...?\", "
    "\"what did I ask you to remember about ...?\", "
    "\"recall my note on the parking spot\", "
    "\"what do you remember about ...?\". The message asks to retrieve "
    "something previously stored."
)

# Key (inside the speaker's `request_data`) carrying telegram-resolved context.
# Telegram attaches the per-chat target bank here; see the telegram `/memory`
# command. Membank itself has NO default-bank concept — it only ever receives an
# explicit bank id.
REQUEST_MEMBANK_KEY = "membank"
REQUEST_DEFAULT_BANK_KEY = "default_bank"

# How many memories a recall answer will render at most (the underlying
# `/memory/list` is itself capped/paginated server-side).
RECALL_RENDER_LIMIT = 10
# How many characters of a memory's content to show in a recall answer.
RECALL_CONTENT_SNIPPET = 400

# Postprocess modes (string values understood by the speaker).
POSTPROCESS_RAW = "RAW"
POSTPROCESS_REWORD = "REWORD"


# ============================= LLM Extraction ============================== #
def _first_json_object(text: str) -> dict:
    """Parses the first JSON object out of an LLM response string.

    LLMs occasionally wrap JSON in prose or code fences; this locates the
    outermost ``{...}`` and parses it. Raises ``MembankInputError`` on failure.
    """
    if not isinstance(text, str):
        raise MembankInputError("LLM response was not a string.")

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        raise MembankInputError("LLM response did not contain a JSON object.")

    snippet = text[start:end + 1]
    try:
        obj = json.loads(snippet)
    except json.JSONDecodeError as e:
        raise MembankInputError(
            "LLM response was not valid JSON: %s" % str(e)) from e

    if not isinstance(obj, dict):
        raise MembankInputError("LLM response was not a JSON object.")
    return obj


def _clean_bank(value) -> str:
    """Normalizes an LLM-provided bank id to a non-empty string or None."""
    if value is None:
        return None
    value = str(value).strip()
    return value if len(value) > 0 else None


def extract_store_fields(dialogue, text: str) -> dict:
    """Uses the LLM to extract explicit STORE fields from a natural-language
    "remember this" message.

    Returns a dict: ``{"name": str, "content": str, "tags": [str, ...],
    "bank": str|None}``. The service will sanitize tags again on write; this
    just produces reasonable candidate values. Raises ``MembankInputError`` on
    unrecoverable failure.
    """
    output_format = {
        "name": "<a short (a few words) human label for this memory>",
        "content": "<the full note/idea to store, cleaned up but faithful>",
        "tags": ["<zero or more short topic tags>"],
        "bank": "<the bank id the user explicitly named, or null>",
    }
    intro = (
        "You are the extraction component of a memory-bank service. "
        "The user wants to STORE a note/idea. Read their message and return a "
        "SINGLE JSON object (and nothing else) with these fields:\n\n"
        "%s\n\n"
        "Rules:\n"
        "- \"name\" is a concise label (<= 256 chars), never empty.\n"
        "- \"content\" is the substance to remember; keep the user's meaning, "
        "do not invent facts.\n"
        "- \"tags\" is a list of short topic keywords (lowercase, letters/"
        "digits/underscores/hyphens). Use an empty list if unsure.\n"
        "- \"bank\" is ONLY set if the user explicitly named a specific bank to "
        "store into; otherwise null.\n"
        "Return only the JSON object."
        % json.dumps(output_format, indent=4)
    )

    response = dialogue.oneshot(intro, text)
    obj = _first_json_object(response)

    name = obj.get("name", None)
    content = obj.get("content", None)
    if not isinstance(name, str) or len(name.strip()) == 0:
        raise MembankInputError("LLM did not produce a memory name.")
    if not isinstance(content, str) or len(content.strip()) == 0:
        # Fall back to the raw user text if the LLM omitted content.
        content = text

    tags = obj.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    # Keep only string tags; the service sanitizer will validate/normalize.
    tags = [str(t) for t in tags if isinstance(t, (str, int))]

    return {
        "name": name.strip(),
        "content": content,
        "tags": tags,
        "bank": _clean_bank(obj.get("bank", None)),
    }


def extract_query_filters(dialogue, text: str, now_ts: int = None) -> dict:
    """Uses the LLM to convert a natural-language recall question into
    structured filters.

    Returns a dict: ``{"tags": [..]|None, "time_range": {"start", "end"}|None,
    "keyword": str|None, "tag_mode": "any"|"all", "bank": str|None}``. Raises
    ``MembankInputError`` on unrecoverable failure.
    """
    if now_ts is None:
        now_ts = int(time.time())
    now_iso = datetime.utcfromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")

    output_format = {
        "tags": ["<zero or more topic tags to match, or omit>"],
        "tag_mode": "any",
        "time_range": {
            "start": "<unix epoch seconds, or omit>",
            "end": "<unix epoch seconds, or omit>",
        },
        "keyword": "<a substring to search names+content, or omit>",
        "bank": "<the bank id the user explicitly named, or null>",
    }
    intro = (
        "You are the query-planning component of a memory-bank service. "
        "The user is asking to RECALL previously stored notes. Convert their "
        "question into a SINGLE JSON object (and nothing else) of structured "
        "filters with these fields:\n\n"
        "%s\n\n"
        "Rules:\n"
        "- The current UTC time is %s (epoch %d). Resolve relative phrases "
        "like \"last month\" or \"yesterday\" into a \"time_range\" of unix "
        "epoch seconds.\n"
        "- \"tags\" are short topic keywords; \"tag_mode\" is \"any\" (default) "
        "or \"all\".\n"
        "- \"keyword\" is a single substring for a free-text search.\n"
        "- Omit any field you cannot confidently infer (do not guess).\n"
        "- \"bank\" is ONLY set if the user explicitly named a specific bank; "
        "otherwise null.\n"
        "Return only the JSON object."
        % (json.dumps(output_format, indent=4), now_iso, now_ts)
    )

    response = dialogue.oneshot(intro, text)
    obj = _first_json_object(response)

    # Tags.
    tags = obj.get("tags", None)
    if isinstance(tags, list):
        tags = [str(t) for t in tags if isinstance(t, (str, int))]
        tags = tags if len(tags) > 0 else None
    else:
        tags = None

    # Tag mode.
    tag_mode = obj.get("tag_mode", "any")
    tag_mode = str(tag_mode).strip().lower()
    if tag_mode not in ("any", "all"):
        tag_mode = "any"

    # Time range.
    time_range = None
    tr = obj.get("time_range", None)
    if isinstance(tr, dict):
        start = _coerce_epoch(tr.get("start", None))
        end = _coerce_epoch(tr.get("end", None))
        if start is not None or end is not None:
            time_range = {}
            if start is not None:
                time_range["start"] = start
            if end is not None:
                time_range["end"] = end

    # Keyword.
    keyword = obj.get("keyword", None)
    if keyword is not None:
        keyword = str(keyword).strip()
        keyword = keyword if len(keyword) > 0 else None

    return {
        "tags": tags,
        "tag_mode": tag_mode,
        "time_range": time_range,
        "keyword": keyword,
        "bank": _clean_bank(obj.get("bank", None)),
    }


def _coerce_epoch(value):
    """Coerces an LLM-provided timestamp into an int epoch, or None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ============================== NLA Helpers =============================== #
def _resolve_default_bank(params: NLAEndpointInvokeParameters):
    """Extracts the telegram-supplied per-chat default bank id (if any) from the
    invocation's `extra_params.request_data`. Returns the bank id or None.
    """
    extra = getattr(params, "extra_params", None)
    if not isinstance(extra, dict):
        return None
    request_data = extra.get("request_data", None)
    if not isinstance(request_data, dict):
        return None
    membank_ctx = request_data.get(REQUEST_MEMBANK_KEY, None)
    if not isinstance(membank_ctx, dict):
        return None
    return _clean_bank(membank_ctx.get(REQUEST_DEFAULT_BANK_KEY, None))


def _user_text(params: NLAEndpointInvokeParameters) -> str:
    """Returns the most specific user text available (substring if present)."""
    if params.has_substring():
        return params.substring
    return params.message


def _need_bank_result(action: str) -> NLAResult:
    """Builds the "which bank?" clarification result (REWORD)."""
    return NLAResult.from_json({
        "success": False,
        "message": "I'm not sure which memory bank to %s. "
                   "Please tell me which bank to use." % action,
        "message_postprocess": POSTPROCESS_REWORD,
    })


# ============================== NLA Handlers =============================== #
def nla_remember(oracle, jdata) -> NLAResult:
    """STORE handler: extract explicit name/tags/content via the LLM, then add
    the memory to the resolved bank (ACL enforced under the invoking account).

    Returns an `NLAResult`. The confirmation is REWORD'd so DImROD phrases it
    naturally (per the architecture report §7.1).
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    text = _user_text(params)

    # Extract explicit fields from the natural-language message.
    try:
        fields = oracle.service.nla_extract_store(text)
    except Exception as e:
        oracle.log.write("nla_remember extraction failed: %s" % str(e))
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't understand what you wanted me to remember.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Resolve the target bank: an explicitly-named bank wins over the chat's
    # configured default.
    bank_id = fields.get("bank", None) or _resolve_default_bank(params)
    if bank_id is None:
        return _need_bank_result("save this to")

    # Enforce write ACL under the invoking service account (flask.g.user).
    bank, status = oracle._acl_write(bank_id)
    if bank is None:
        if status == 403:
            msg = "I don't have permission to write to that memory bank."
        else:
            msg = "I couldn't find a memory bank called \"%s\"." % bank_id
        return NLAResult.from_json({
            "success": False,
            "message": msg,
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Add the memory with EXPLICIT values (tags sanitized server-side).
    try:
        memory = oracle.service.dispatch(
            bank.add_memory,
            name=fields["name"],
            content=fields["content"],
            tags=fields.get("tags", []),
        )
    except MembankInputError as e:
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't save that memory: %s" % e.message,
            "message_postprocess": POSTPROCESS_REWORD,
        })
    except Exception as e:
        oracle.log.write("nla_remember add failed (bank=%s): %s" %
                         (bank.config.id, str(e)))
        return NLAResult.from_json({
            "success": False,
            "message": "Something went wrong while saving that memory.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Plain-text confirmation (no HTML): safe to REWORD into DImROD's voice.
    message = "Saved to the \"%s\" memory bank as \"%s\"." % (
        bank.config.name, fields["name"])
    return NLAResult.from_json({
        "success": True,
        "message": message,
        "message_postprocess": POSTPROCESS_REWORD,
        "payload": {"id": memory.id, "bank": bank.config.id},
    })


def nla_recall(oracle, jdata) -> NLAResult:
    """QUERY handler: convert the question into structured filters via the LLM,
    list matching memories from the resolved bank (ACL enforced), and format a
    faithful answer.

    Returns an `NLAResult`. The formatted answer is RAW (already-escaped
    Telegram HTML) so the user's stored notes are recalled verbatim rather than
    being paraphrased by the rewording LLM.
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    text = _user_text(params)

    # Convert the question into structured filters.
    try:
        query = oracle.service.nla_extract_query(text)
    except Exception as e:
        oracle.log.write("nla_recall extraction failed: %s" % str(e))
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't work out what to search for.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Resolve the target bank (explicit bank wins over the chat default).
    bank_id = query.get("bank", None) or _resolve_default_bank(params)
    if bank_id is None:
        return _need_bank_result("search")

    # Enforce read ACL under the invoking service account (flask.g.user).
    bank = oracle._acl_read(bank_id)
    if bank is None:
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't find a memory bank called \"%s\"." % bank_id,
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Build the structured filter payload for /memory/list.
    filters = {}
    if query.get("tags"):
        filters["tags"] = query["tags"]
        filters["tag_mode"] = query.get("tag_mode", "any")
    if query.get("time_range"):
        filters["time_range"] = query["time_range"]
    if query.get("keyword"):
        filters["keyword"] = query["keyword"]

    try:
        memories, total = oracle.service.dispatch(
            bank.list_memories, filters=filters, limit=RECALL_RENDER_LIMIT,
            order="desc")
    except MembankInputError as e:
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't search that bank: %s" % e.message,
            "message_postprocess": POSTPROCESS_REWORD,
        })
    except Exception as e:
        oracle.log.write("nla_recall list failed (bank=%s): %s" %
                         (bank.config.id, str(e)))
        return NLAResult.from_json({
            "success": False,
            "message": "Something went wrong while searching that bank.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    if len(memories) == 0:
        return NLAResult.from_json({
            "success": True,
            "message": "I couldn't find anything about that.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    message = _format_recall(bank, memories, total)
    return NLAResult.from_json({
        "success": True,
        "message": message,
        "message_postprocess": POSTPROCESS_RAW,
    })


def _format_recall(bank, memories: list, total: int) -> str:
    """Formats recalled memories into Telegram HTML.

    Every dynamic value is HTML-escaped so stored notes containing '&', '<', or
    '>' cannot break Telegram's HTML parser. The result is returned RAW (see
    `nla_recall`).
    """
    shown = len(memories)
    header = "<b>Found %d matching memor%s in \"%s\":</b>\n" % (
        total,
        "y" if total == 1 else "ies",
        html.escape(bank.config.name),
    )
    if total > shown:
        header += "<i>(showing the %d most recent)</i>\n" % shown

    blocks = []
    for mem in memories:
        data = mem.to_api_dict()
        name = html.escape(str(data.get("name", "")))
        when = datetime.utcfromtimestamp(
            int(data.get("timestamp", 0))).strftime("%Y-%m-%d")

        content = str(data.get("content", ""))
        if len(content) > RECALL_CONTENT_SNIPPET:
            content = content[:RECALL_CONTENT_SNIPPET].rstrip() + "…"
        content = html.escape(content)

        block = "\n· <b>%s</b> <i>(%s)</i>\n%s" % (name, when, content)

        tags = data.get("tags", [])
        if tags:
            tag_str = " ".join("#%s" % html.escape(str(t)) for t in tags)
            block += "\n<code>%s</code>" % tag_str

        blocks.append(block)

    return header + "".join(blocks)
