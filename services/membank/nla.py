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
import flask
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
    "Use for phrases like: "
    "\"remember this: ...\", "
    "\"note that ...\", "
    "\"here's an idea: ...\", "
    "\"write this down: ...\", "
    "\"save this memory ...\", "
    "\"don't let me forget ...\". "
    "The message contains something the user wants persisted so it can be recalled later."
)

NLA_RECALL_NAME = "recall"
NLA_RECALL_DESC = (
    "Recall previously stored memories, notes, or ideas. "
    "Use this when the user is asking ANY question involving \"what\", \"where\", \"when\", \"who\", \"how\", \"why\"."
    "Examples: "
    "\"what did I tell you last month about ...?\", "
    "\"where is...?\", "
    "\"what was ...?\", "
    "\"what did I ask you to remember about ...?\", "
    "\"what did I say my parking spot was?\", "
    "\"what do you remember about ...?\", "
    "\"can you search the logs for ...?\", "
    "\"do I have anything saved about ...?\". "
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

# Upper bound on how many banks are enumerated in a dynamic NLA description or a
# clarification's bank list, so the router prompt / clarification stays bounded.
NLA_DESC_BANK_CAP = 12

# Postprocess modes (string values understood by the speaker).
POSTPROCESS_RAW = "RAW"
POSTPROCESS_REWORD = "REWORD"

# Upper bound on how many of a bank's existing tags are surfaced to the store
# extraction LLM as reuse suggestions, so the prompt stays bounded. `list_tags`
# returns tags sorted by refcount desc, so the most common tags are kept.
STORE_TAG_SUGGESTION_CAP = 100


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


def _format_tag_suggestions(existing_tags, cap: int = STORE_TAG_SUGGESTION_CAP):
    """Builds the "existing tags" suggestion block appended to the STORE
    extraction prompt, or an empty string when there is nothing to suggest.

    De-duplicates while preserving order and caps the list at `cap` so the
    prompt stays bounded (input is expected sorted by refcount desc, so the most
    common tags are kept). Returns a plain-text sentence (no trailing newline).
    """
    if not existing_tags:
        return ""
    seen = []
    for tag in existing_tags:
        if not isinstance(tag, str):
            tag = str(tag)
        tag = tag.strip()
        if len(tag) == 0 or tag in seen:
            continue
        seen.append(tag)
        if len(seen) >= cap:
            break
    if len(seen) == 0:
        return ""
    return (
        "Existing tags in this bank (PREFER reusing an existing tag when it "
        "fits; only create a new tag if none apply): %s" % ", ".join(seen)
    )


def extract_store_fields(dialogue, text: str, existing_tags: list = None) -> dict:
    """Uses the LLM to extract explicit STORE fields from a natural-language
    "remember this" message.

    Returns a dict: ``{"name": str, "content": str, "tags": [str, ...],
    "bank": str|None}``. The service will sanitize tags again on write; this
    just produces reasonable candidate values. Raises ``MembankInputError`` on
    unrecoverable failure.

    `existing_tags` (if provided) is a list of the target bank's current tags;
    they are surfaced to the LLM as reuse suggestions so it prefers existing
    tags over inventing near-duplicates. The section is omitted when the list is
    empty and never changes what is ultimately stored or how tags are
    sanitized.
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

    # Surface the bank's existing tags as reuse suggestions (omitted when none).
    suggestions = _format_tag_suggestions(existing_tags)
    if suggestions:
        intro += "\n\n" + suggestions

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


def general_answer(dialogue, text: str) -> str:
    """Runs a single, general-purpose LLM completion that answers the user's
    question concisely.

    This is used by `nla_recall` on a MISS (no stored memories matched): rather
    than deferring to the speaker, the recall handler answers the user's
    ORIGINAL question directly with a short completion using the SAME membank
    dialogue that drives the extraction oneshots. The returned string is the raw
    LLM text (the caller is responsible for any HTML-escaping). Raises on an
    unrecoverable LLM failure so the caller can fall back gracefully.
    """
    intro = (
        "You are a concise, helpful assistant. Answer the user's question "
        "directly and briefly in plain text. Do not add markup, headings, or "
        "preamble; just answer."
    )
    return dialogue.oneshot(intro, text)


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


def _need_bank_result(action: str, banks: list = None) -> NLAResult:
    """Builds the "which bank?" clarification result (REWORD).

    When `banks` is provided, the accessible banks are enumerated (bounded by
    `NLA_DESC_BANK_CAP`) so the user can pick one. This is used both for the
    no-bank case and for the named-but-unresolved case (where the utterance
    named a bank that did not resolve to an accessible one — we clarify rather
    than silently falling through to a default).
    """
    msg = "I'm not sure which memory bank to %s. " % action
    if banks:
        msg += "You have access to these memory banks: %s. " % \
               _bank_names_phrase(banks)
    msg += "Please tell me which bank to use."
    return NLAResult.from_json({
        "success": False,
        "message": msg,
        "message_postprocess": POSTPROCESS_REWORD,
    })


def _bank_names_phrase(banks: list, cap: int = NLA_DESC_BANK_CAP) -> str:
    """Renders a compact, bounded, comma-separated list of banks as
    ``"id" (Name)`` entries, appending ``(+K more)`` when truncated. Plain text
    (no HTML) — intended for REWORD clarifications and dynamic descriptions.
    """
    shown = banks[:cap]
    parts = ["\"%s\" (%s)" % (b.config.id, b.config.name) for b in shown]
    phrase = ", ".join(parts)
    extra = len(banks) - len(shown)
    if extra > 0:
        phrase += ", (+%d more)" % extra
    return phrase


def _render_bank_catalog(banks: list, cap: int = NLA_DESC_BANK_CAP) -> str:
    """Builds the suffix appended to a dynamic NLA description enumerating the
    banks accessible to the requesting user. Bounded by `cap` to keep the
    router prompt small. Returns a leading-space-prefixed sentence, or a short
    note when the user has access to no banks.
    """
    if not banks:
        return " (You currently have access to no memory banks.)"
    return " Available memory banks: %s." % _bank_names_phrase(banks, cap=cap)


# ============================== NLA Handlers =============================== #
def nla_remember(oracle, jdata) -> NLAResult:
    """STORE handler: extract explicit name/tags/content via the LLM, then add
    the memory to the resolved bank (ACL enforced under the invoking account).

    Returns an `NLAResult`. The confirmation is REWORD'd so DImROD phrases it
    naturally (per the architecture report §7.1).
    """
    params = NLAEndpointInvokeParameters.from_json(jdata)
    text = _user_text(params)

    # Suggest the DEFAULT target bank's EXISTING tags to the extraction LLM so
    # it reuses them instead of inventing near-duplicate tags. The explicitly-
    # named bank is only known AFTER extraction, so we resolve the DEFAULT
    # target bank (per-request default -> service default) via the shared
    # resolver WITHOUT a named ref, keeping this to a SINGLE extraction call.
    # ACLs are respected: `resolve_nla_bank` only returns a bank the caller may
    # use, and its tags come from the same in-process `list_tags` path that
    # backs `/m tags`. If no default bank resolves/accessible (or the lookup
    # fails), we simply omit suggestions and NEVER block the store.
    # NOTE: the default bank is resolved with require_write=True (it checks
    # `can_write`, NOT `can_read`). Reading its tags is an incidental READ, so we
    # additionally gate the `list_tags` read on `can_read` — a bank a caller may
    # WRITE but not READ (an atypical but config-permitted ACL, since
    # `write_users` and `read_users` are independent lists) must never have its
    # tag names surfaced to a caller lacking read authorization.
    existing_tags = []
    try:
        sugg_bank, _ = oracle.resolve_nla_bank(params, None, require_write=True)
        username = flask.g.user.config.username
        if sugg_bank is not None and sugg_bank.can_read(username):
            tag_rows = oracle.service.dispatch(sugg_bank.list_tags)
            existing_tags = [row["tag"] for row in tag_rows
                             if isinstance(row, dict) and row.get("tag")]
    except Exception as e:
        oracle.log.write("nla_remember tag-suggestion lookup failed: %s" %
                         str(e))
        existing_tags = []

    # Extract explicit fields from the natural-language message.
    try:
        fields = oracle.service.nla_extract_store(text,
                                                  existing_tags=existing_tags)
    except Exception as e:
        oracle.log.write("nla_remember extraction failed: %s" % str(e))
        return NLAResult.from_json({
            "success": False,
            "message": "I couldn't understand what you wanted me to remember.",
            "message_postprocess": POSTPROCESS_REWORD,
        })

    # Resolve the target bank via the shared precedence resolver:
    #   (a) a bank named in the utterance  ->
    #   (b) the per-request (telegram per-chat) default  ->
    #   (c) the service-level default  ->
    #   (d) a "which bank?" clarification.
    # The returned bank is already write-ACL-checked for the invoking account; a
    # named-but-unresolved reference yields a clarification (no silent fallback).
    bank, err = oracle.resolve_nla_bank(params, fields.get("bank", None),
                                        require_write=True)
    if bank is None:
        return err

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
    message = "I saved a note to the \"%s\" memory bank. The note is titled: \"%s\"." % (
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

    Returns an `NLAResult`. On a HIT the formatted answer is RAW (already-
    escaped Telegram HTML) so the user's stored notes are recalled verbatim
    rather than being paraphrased. On a MISS the handler composes a RAW,
    HTML-safe "<notice>\n\n<general LLM answer>" itself (falling back to the
    notice alone if the completion fails). Hard extraction/search errors return
    a clear REWORD'd error.
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

    # Resolve the target bank via the shared precedence resolver (read ACL); a
    # named-but-unresolved reference yields a clarification (no silent fallback).
    bank, err = oracle.resolve_nla_bank(params, query.get("bank", None),
                                        require_write=False)
    if bank is None:
        return err

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

    # Recall outcomes:
    #   HIT  -> return ONLY the membank findings (RAW, HTML-escaped).
    #   MISS -> a SHORT "no results" notice FOLLOWED BY a general-purpose LLM
    #           answer to the user's ORIGINAL question, composed here into ONE
    #           RAW message. If the completion fails, the notice surfaces alone.
    #   error -> a REWORD'd error, no completion (handled above).
    #
    # This behavior is entirely self-contained within the recall handler: the
    # speaker performs no special completion logic. On a MISS we run a quick
    # general completion via the SAME membank dialogue used for extraction, then
    # compose "<notice>\n\n<answer>" as a single RAW (HTML-safe) message. The
    # notice is fixed plain text; the completion text is HTML-escaped so it is
    # safe for the downstream Telegram HTML renderer, matching the escaping used
    # for the findings section. A completion failure never crashes the NLA — the
    # notice is returned by itself (the error is logged). This is a SUCCESS.
    if len(memories) == 0:
        notice = "I didn't find anything in the memory bank."
        answer = None
        try:
            answer = oracle.service.nla_general_answer(text)
        except Exception as e:
            oracle.log.write("nla_recall miss completion failed: %s" % str(e))
            answer = None

        # The notice is a fixed constant with no HTML-special characters. The
        # completion text is untrusted, so it is HTML-escaped (matching the
        # findings escaping) before being appended, keeping the composed RAW
        # message safe for the downstream Telegram HTML renderer.
        message = notice
        if isinstance(answer, str) and len(answer.strip()) > 0:
            message += "\n\n" + html.escape(answer.strip())

        return NLAResult.from_json({
            "success": True,
            "message": message,
            "message_postprocess": POSTPROCESS_RAW,
        })

    # Results found: return the RAW findings section ONLY. On a hit the memories
    # ARE the answer, so no LLM completion runs.
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
    header = "<b>Found %d matching memor%s in \"%s\":</b>" % (
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
        when = datetime.utcfromtimestamp(int(data.get("timestamp", 0))).strftime("%Y-%m-%d %H:%M:%S UTC")

        content = str(data.get("content", ""))
        if len(content) > RECALL_CONTENT_SNIPPET:
            content = content[:RECALL_CONTENT_SNIPPET].rstrip() + "…"
        content = html.escape(content)

        block = "\n<b>%s</b> (%s)\n<i>%s</i>" % (name, when, content)

        tags = data.get("tags", [])
        if tags:
            tag_str = " ".join("#%s" % html.escape(str(t)) for t in tags)
            block += "\n%s" % tag_str

        blocks.append("\n" + block)

    return header + "".join(blocks)
