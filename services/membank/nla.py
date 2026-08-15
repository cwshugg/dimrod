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
from models import MembankInputError, sanitize_tag


# ================================ Constants ================================ #
# NLA endpoint identifiers + router trigger descriptions.
NLA_REMEMBER_NAME = "remember"
NLA_REMEMBER_DESC = (
    "Store a new memory, note, or idea for later recall. "
    "Use this when the user is stating a fact or idea. "
    "The user may not necessarily be explicit about \"remember this\" or \"store this\", but the intent is to persist information for later retrieval. "
    "Example phrases: "
    "\"I parked in spot 207.\", "
    "\"remember this: ...\", "
    "\"Bob's birthday is December 2nd.\", "
    "\"note that ...\", "
    "\"Jimmy wants to try the thai restaurant next time he visits.\", "
    "\"here's an idea: ...\", "
    "\"write this down: ...\", "
    "\"Trivia starts at 8:00pm every Tuesday night until the end of summer.\", "
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

# Multi-keyword extraction bounds (see design report ab8428ad682bed19 §3.1).
# `keywords` is a list of short search terms the query LLM extracts (plus light
# inflection/synonym expansion). These bounds keep the SQL OR-group and the
# relevance-filter payload small and predictable regardless of model variance.
KEYWORD_MAX = 12        # cap on emitted keywords (mirrors models.KEYWORD_SQL_CAP)
KEYWORD_MIN_LEN = 2     # drop keyword terms shorter than this

# Belt-and-suspenders stopword guard: even though the prompt asks the LLM to
# omit these, we filter them server-side so a leaked stopword never widens the
# net into noise. Question words, articles, pronouns, common temporal words,
# and a few high-frequency verbs/prepositions.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
    "i", "me", "my", "mine", "we", "us", "our", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them",
    "their", "this", "that", "these", "those",
    "what", "when", "where", "who", "whom", "whose", "which", "why", "how",
    "did", "do", "does", "done", "is", "are", "was", "were", "be", "been",
    "being", "am", "have", "has", "had", "will", "would", "can", "could",
    "should", "shall", "may", "might", "must",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "about",
    "into", "over", "under", "up", "down", "out", "off",
    "earlier", "today", "yesterday", "tomorrow", "now", "later", "recently",
    "ago", "morning", "afternoon", "evening", "tonight", "night",
    "please", "tell", "say", "said", "get", "got", "there", "here",
})

# Recall retrieval / relevance-filter bounds (design report §3.5).
RECALL_CANDIDATE_CAP = 25   # max candidates retrieved + sent to relevance filter
RELEVANCE_SNIPPET = 300     # chars of content per candidate in the filter prompt

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


def _sanitize_keywords(values) -> list:
    """Sanitizes a raw list of LLM-provided keyword terms into a clean list.

    Coerces each entry to a lowercase, stripped string; drops empties, terms
    shorter than ``KEYWORD_MIN_LEN``, and stopwords (``STOPWORDS``); de-dupes
    while preserving order; caps at ``KEYWORD_MAX``. Returns ``[]`` when nothing
    survives (the caller decides whether that means ``None``).
    """
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for v in values:
        if not isinstance(v, (str, int)):
            continue
        term = str(v).strip().lower()
        if len(term) < KEYWORD_MIN_LEN:
            continue
        if term in STOPWORDS:
            continue
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
        if len(out) >= KEYWORD_MAX:
            break
    return out


def extract_query_filters(dialogue, text: str, now_ts: int = None) -> dict:
    """Uses the LLM to convert a natural-language recall question into
    structured filters.

    Returns a dict: ``{"tags": [..]|None, "time_range": {"start", "end"}|None,
    "keyword": str|None, "keywords": [str, ...]|None, "tag_mode": "any"|"all",
    "bank": str|None}``. Raises ``MembankInputError`` on unrecoverable failure.

    The ``keywords`` list (new) is a set of short, meaningful, sanitized search
    terms (single lowercase words plus light inflection/synonym variants the LLM
    can infer). It casts a WIDER retrieval net than a single substring
    ``keyword`` (kept for back-compat). See design report ab8428ad682bed19 §3.1.
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
        "keywords": ["<individual lowercase search words, or omit>"],
        "keyword": "<a single legacy substring, or omit>",
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
        "- When the time phrase is COARSE/day-granular (\"today\", "
        "\"yesterday\", \"this morning\"), PAD the range by about 12 hours on "
        "each side so a note the user considers \"today\" in their local "
        "timezone is not excluded by a UTC day boundary.\n"
        "- \"tags\" are short topic keywords; \"tag_mode\" is \"any\" (default) "
        "or \"all\".\n"
        "- \"keywords\" is the IMPORTANT field: break the question into the KEY "
        "nouns/verbs the user is really asking about and return them as "
        "individual lowercase words. INCLUDE obvious inflections and synonyms "
        "as separate entries (e.g. park, parked, parking; car, vehicle). "
        "DO NOT include stopwords or question words (where/what/when/did/i/my/"
        "the/a/an/is/was/earlier/today/...). DO NOT include the whole phrase. "
        "Return at most %d keywords.\n"
        "- \"keyword\" is an OPTIONAL single legacy substring for free-text "
        "search; you may omit it when you provide \"keywords\".\n"
        "- Omit any field you cannot confidently infer (do not guess).\n"
        "- \"bank\" is ONLY set if the user explicitly named a specific bank; "
        "otherwise null.\n"
        "Return only the JSON object."
        % (json.dumps(output_format, indent=4), now_iso, now_ts, KEYWORD_MAX)
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

    # Keyword (legacy single substring, kept for back-compat).
    keyword = obj.get("keyword", None)
    if keyword is not None:
        keyword = str(keyword).strip()
        keyword = keyword if len(keyword) > 0 else None

    # Keywords (new multi-term list). Sanitize the LLM's list; if it produced
    # none but DID emit a legacy `keyword`, backfill by tokenizing that string
    # on whitespace through the same sanitation. If neither survives, None.
    keywords = _sanitize_keywords(obj.get("keywords", None))
    if not keywords and keyword is not None:
        keywords = _sanitize_keywords(keyword.split())
    keywords = keywords if len(keywords) > 0 else None

    return {
        "tags": tags,
        "tag_mode": tag_mode,
        "time_range": time_range,
        "keyword": keyword,
        "keywords": keywords,
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


def filter_relevant_candidates(dialogue, question: str, candidates: list,
                               now_ts: int = None) -> list:
    """Uses ONE bounded LLM call to filter recall candidates down to those that
    are actually relevant to the user's question.

    `candidates` is a list of `Memory` objects (already ACL-gated by the
    caller). This performs ID-SELECTION ONLY: the LLM is shown a compact record
    per candidate (id/name/content-snippet/timestamp/tags) plus the ORIGINAL
    question and the current UTC time (so it can reason about time-relative
    phrasing like "earlier today"), and returns the ids of the relevant ones.
    Stored content is NEVER taken from the LLM — only the id selection is used
    to map back to the original verbatim `Memory` objects.

    Returns the kept `Memory` objects in the LLM's relevance order, de-duped,
    guarded against hallucinated ids, and truncated to ``RECALL_RENDER_LIMIT``.
    An empty return means the filter ran and chose none. Raises on an
    unrecoverable LLM/parse failure so the caller can degrade to raw candidates
    (never a false MISS). See design report ab8428ad682bed19 §3.5.
    """
    if not candidates:
        return []
    if now_ts is None:
        now_ts = int(time.time())
    now_iso = datetime.utcfromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")

    # Build the bounded candidate records (cap the number shown to the model).
    by_id = {}
    records = []
    for mem in candidates[:RECALL_CANDIDATE_CAP]:
        data = mem.to_api_dict()
        mid = str(data.get("id", ""))
        if len(mid) == 0:
            continue
        by_id[mid] = mem
        content = str(data.get("content", ""))
        if len(content) > RELEVANCE_SNIPPET:
            content = content[:RELEVANCE_SNIPPET].rstrip() + "…"
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        records.append({
            "id": mid,
            "name": str(data.get("name", "")),
            "content": content,
            "timestamp": int(data.get("timestamp", 0)),
            "tags": [str(t) for t in tags[:8]],
        })

    output_format = {"relevant_ids": ["<id of a relevant candidate>", "..."]}
    intro = (
        "You are the relevance filter of a memory recall service. Given the "
        "user's question and a list of candidate stored memories, return the "
        "IDs of ONLY those candidates that actually answer or are directly "
        "relevant to the question. Consider each candidate's timestamp for "
        "time-relative questions (the current UTC time is %s, epoch %d). Do "
        "NOT rewrite or summarize memories. If none are relevant, return an "
        "empty list.\n\n"
        "Return a SINGLE JSON object (and nothing else) of this form:\n%s\n\n"
        "Candidates (JSON):\n%s"
        % (now_iso, now_ts, json.dumps(output_format, indent=4),
           json.dumps(records, indent=4))
    )

    response = dialogue.oneshot(intro, question)
    obj = _first_json_object(response)

    relevant = obj.get("relevant_ids", None)
    if not isinstance(relevant, list):
        raise MembankInputError(
            "relevance filter did not return a \"relevant_ids\" list.")

    # Keep only ids that exist in the candidate set (guards hallucinated ids),
    # preserve the LLM's order, de-dupe. Map back to verbatim Memory objects.
    kept = []
    seen = set()
    for rid in relevant:
        rid = str(rid).strip()
        if rid in seen or rid not in by_id:
            continue
        seen.add(rid)
        kept.append(by_id[rid])
        if len(kept) >= RECALL_RENDER_LIMIT:
            break
    return kept


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

    # Build the structured filter payload for the INITIAL (narrowing) pass.
    # The new `keywords` list widens name/content matching (OR-group); the
    # legacy `keyword` is still forwarded for continuity.
    filters = {}
    if query.get("tags"):
        filters["tags"] = query["tags"]
        filters["tag_mode"] = query.get("tag_mode", "any")
    if query.get("time_range"):
        filters["time_range"] = query["time_range"]
    if query.get("keyword"):
        filters["keyword"] = query["keyword"]
    if query.get("keywords"):
        filters["keywords"] = query["keywords"]

    # Retrieval: an initial narrowing pass, then a WIDENED fallback if it finds
    # nothing (time dropped; keywords over name/content UNION keywords as
    # OR-tags — two cheap SQL reads, ZERO extra LLM calls). Both paths produce a
    # single `candidates` list (<= RECALL_CANDIDATE_CAP) handed to the relevance
    # filter before any HIT/MISS decision.
    try:
        candidates = _recall_retrieve(oracle, bank, query, filters)
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

    # Nothing retrieved even after widening -> a real MISS. The relevance filter
    # is NOT called (no candidates), so recall spends zero filter calls here.
    if len(candidates) == 0:
        return _recall_miss(oracle, text)

    # Relevance filter (the precision gate): ONE bounded LLM call over the
    # candidates + the ORIGINAL question, returning the ids to keep. This is the
    # AT-MOST-ONE extra LLM call recall may add.
    #
    #   >=1 kept        -> HIT (RAW findings, verbatim/escaped).
    #   0 kept (ran ok) -> MISS (notice + general answer).
    #   filter FAILURE  -> degrade to the RAW candidates (never a false MISS).
    try:
        kept = oracle.service.nla_filter_relevance(text, candidates)
    except Exception as e:
        # A filter outage must degrade to today's behavior (show what we found),
        # NOT to a false MISS. Show the raw candidates (newest-first, capped).
        oracle.log.write("nla_recall relevance filter failed (bank=%s): %s" %
                         (bank.config.id, str(e)))
        message = _format_recall(bank, candidates[:RECALL_RENDER_LIMIT])
        return NLAResult.from_json({
            "success": True,
            "message": message,
            "message_postprocess": POSTPROCESS_RAW,
        })

    # The filter ran and chose none -> MISS (self-contained notice + answer).
    if not kept:
        return _recall_miss(oracle, text)

    # Kept >= 1 -> HIT. The findings ARE the answer, so no completion runs.
    message = _format_recall(bank, kept)
    return NLAResult.from_json({
        "success": True,
        "message": message,
        "message_postprocess": POSTPROCESS_RAW,
    })


def _recall_retrieve(oracle, bank, query: dict, filters: dict) -> list:
    """Runs the recall retrieval: an initial narrowing `list_memories` pass and,
    when that returns nothing, a WIDENED fallback pass.

    The fallback drops `time_range` and issues TWO cheap SQL reads which are
    unioned in Python (design report ab8428ad682bed19 §3.4):
      * W1 — the extracted keywords matched over name/content (OR-group).
      * W2 — the same keywords (plus any extracted tags) matched as OR-tags;
              this is the ONE place keyword<->tag matching is enabled.
    The union is newest-first and capped at ``RECALL_CANDIDATE_CAP``. ZERO extra
    LLM calls are made here. Returns a list of `Memory` candidates (possibly
    empty). Propagates `list_memories` errors to the caller.
    """
    memories, _ = oracle.service.dispatch(
        bank.list_memories, filters=filters, limit=RECALL_CANDIDATE_CAP,
        order="desc")
    if len(memories) > 0:
        return memories[:RECALL_CANDIDATE_CAP]

    # Determine the keyword terms to widen with (backfill from legacy keyword).
    wide_terms = query.get("keywords")
    if not wide_terms and query.get("keyword"):
        wide_terms = _sanitize_keywords(str(query["keyword"]).split())
    base_tags = query.get("tags") or []

    # Nothing to widen on (no keywords AND no tags) -> no fallback.
    if not wide_terms and not base_tags:
        return []

    unioned = []
    seen = set()

    def _merge(rows):
        for m in rows:
            if m.id in seen:
                continue
            seen.add(m.id)
            unioned.append(m)

    # W1: keywords over name/content only (no tags, no time).
    if wide_terms:
        w1, _ = oracle.service.dispatch(
            bank.list_memories, filters={"keywords": list(wide_terms)},
            limit=RECALL_CANDIDATE_CAP, order="desc")
        _merge(w1)

    # W2: keywords (+ extracted tags) as OR-tags (no keywords, no time). Only
    # terms that are VALID tags are used; keyword-derived terms that cannot be a
    # tag (e.g. start with a digit) are dropped individually so one bad term
    # never aborts the widened tag read.
    tag_terms = []
    tag_seen = set()
    for t in list(base_tags) + list(wide_terms or []):
        try:
            st = sanitize_tag(t)
        except MembankInputError:
            continue
        if st in tag_seen:
            continue
        tag_seen.add(st)
        tag_terms.append(st)
    if tag_terms:
        w2, _ = oracle.service.dispatch(
            bank.list_memories,
            filters={"tags": tag_terms, "tag_mode": "any"},
            limit=RECALL_CANDIDATE_CAP, order="desc")
        _merge(w2)

    # Newest-first across the union, capped.
    unioned.sort(key=lambda m: (int(m.timestamp), m.id), reverse=True)
    return unioned[:RECALL_CANDIDATE_CAP]


def _recall_miss(oracle, text: str) -> NLAResult:
    """Builds the self-contained recall MISS result: a SHORT "no results" notice
    FOLLOWED BY a general-purpose LLM answer to the user's ORIGINAL question,
    composed into ONE RAW (HTML-safe) message.

    This behavior is entirely self-contained within the recall handler (the
    speaker performs no special completion logic). The notice is a fixed
    constant with no HTML-special characters; the completion text is untrusted
    and HTML-escaped (matching the findings escaping) before being appended. A
    completion failure never crashes the NLA — the notice surfaces alone (the
    error is logged). This is a SUCCESS.
    """
    notice = "I didn't find anything in the memory bank."
    answer = None
    try:
        answer = oracle.service.nla_general_answer(text)
    except Exception as e:
        oracle.log.write("nla_recall miss completion failed: %s" % str(e))
        answer = None

    message = notice
    if isinstance(answer, str) and len(answer.strip()) > 0:
        message += "\n\n" + html.escape(answer.strip())

    return NLAResult.from_json({
        "success": True,
        "message": message,
        "message_postprocess": POSTPROCESS_RAW,
    })


def _format_recall(bank, memories: list) -> str:
    """Formats recalled memories into Telegram HTML.

    Every dynamic value is HTML-escaped so stored notes containing '&', '<', or
    '>' cannot break Telegram's HTML parser. The result is returned RAW (see
    `nla_recall`).
    """
    count = len(memories)
    header = "<b>Found %d matching memor%s in \"%s\":</b>" % (
        count,
        "y" if count == 1 else "ies",
        html.escape(bank.config.name),
    )

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
