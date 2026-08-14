#!/usr/bin/python3
"""Unit tests for the /memory (/m) Telegram command.

The command is purely subcommand-driven: each subcommand maps 1:1 onto a
membank oracle endpoint (no speaker/LLM). These tests exercise the field
parser, per-subcommand payload construction (with a mocked membank session),
HTML rendering, default-bank resolution, search-filter parsing, edit partial
updates, help/unknown handling, and ACL/error surfacing.
"""

import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_this_dir = os.path.dirname(os.path.realpath(__file__))
_svc_dir = os.path.dirname(_this_dir)
if _svc_dir not in sys.path:
    sys.path.insert(0, _svc_dir)
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

import commands.memory as memory  # noqa: E402
from commands.memory import command_memory  # noqa: E402


# ===========================================================================
# Fakes / helpers
# ===========================================================================
class FakeResp:
    """A minimal stand-in for an HTTP response object."""

    def __init__(self, success=True, status=200, payload=None, message=None):
        self.success = success
        self.status_code = status
        self.payload = payload if payload is not None else {}
        self.message = message


class FakeSession:
    """A fake OracleSession recording POSTs and returning canned responses.

    ``responses`` maps an endpoint path to a ``FakeResp``. A single ``FakeResp``
    (or None) is used as the default for any endpoint.
    """

    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = default if default is not None else FakeResp()
        self.calls = []  # list of (endpoint, payload)

    def post(self, endpoint, payload=None):
        self.calls.append((endpoint, payload))
        return self.responses.get(endpoint, self.default)

    # Mirror OracleSession's static response helpers (callable on instances).
    def get_response_success(self, r):
        return r.success

    def get_response_json(self, r):
        return r.payload

    def get_response_message(self, r):
        return r.message

    # Convenience for assertions.
    def last_payload(self):
        return self.calls[-1][1]

    def last_endpoint(self):
        return self.calls[-1][0]


def _make_message(chat_id=111, text="/m banks"):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.message_id = 42
    msg.text = text
    return msg


def _make_service(bank_id="personal"):
    service = MagicMock()
    service.get_chat_memory_bank = MagicMock(return_value=bank_id)
    service.send_message = MagicMock()
    return service


def _last_text(service):
    """Returns the text of the most recent send_message call."""
    return service.send_message.call_args[0][1]


def _run(service, text, session=None):
    """Runs command_memory for the given raw text, patching the membank
    session helper to return ``session`` (a FakeSession) when provided.
    """
    msg = _make_message(chat_id=111, text=text)
    args = text.split()
    if session is None:
        return command_memory(service, msg, args)
    with patch.object(memory, "_get_membank_session", return_value=session):
        return command_memory(service, msg, args)


# ===========================================================================
# Field parsing
# ===========================================================================
class TestParseFields:
    def test_splits_on_dot(self):
        assert memory._parse_fields("a.b.c") == ["a", "b", "c"]

    def test_trims_whitespace_around_dots(self):
        assert memory._parse_fields("a . b . c") == ["a", "b", "c"]
        assert memory._parse_fields("  lore .  kw=x . limit=5 ") == \
            ["lore", "kw=x", "limit=5"]

    def test_empty_string_yields_single_empty_field(self):
        assert memory._parse_fields("") == [""]


class TestParseKV:
    def test_trims_around_equals(self):
        assert memory._parse_kv("kw = mithril") == ("kw", "mithril")
        assert memory._parse_kv("kw=mithril") == ("kw", "mithril")

    def test_lowercases_key_only(self):
        assert memory._parse_kv("Mode = ANY") == ("mode", "ANY")

    def test_no_equals_returns_none_value(self):
        assert memory._parse_kv("banks") == ("banks", None)

    def test_value_may_contain_equals(self):
        assert memory._parse_kv("content = a=b") == ("content", "a=b")


class TestToEpoch:
    def test_bare_epoch(self):
        assert memory._to_epoch("1704067200") == (1704067200, None)

    def test_date_only_utc(self):
        # 2024-01-01T00:00:00Z == 1704067200
        assert memory._to_epoch("2024-01-01") == (1704067200, None)

    def test_datetime_utc(self):
        assert memory._to_epoch("2024-01-01 00:00:00") == (1704067200, None)

    def test_invalid_returns_error(self):
        epoch, err = memory._to_epoch("not-a-date")
        assert epoch is None
        assert err is not None


# ===========================================================================
# Bank resolution (field 0)
# ===========================================================================
class TestResolveBank:
    def test_explicit_bank(self):
        svc = _make_service(bank_id="personal")
        msg = _make_message()
        assert memory._resolve_bank(svc, msg, ["lore"]) == ("lore", None)

    def test_empty_uses_default(self):
        svc = _make_service(bank_id="personal")
        msg = _make_message()
        assert memory._resolve_bank(svc, msg, [""]) == ("personal", None)

    def test_dash_uses_default(self):
        svc = _make_service(bank_id="personal")
        msg = _make_message()
        assert memory._resolve_bank(svc, msg, ["-"]) == ("personal", None)

    def test_no_default_configured_errors(self):
        svc = _make_service(bank_id=None)
        msg = _make_message()
        bank, err = memory._resolve_bank(svc, msg, ["-"])
        assert bank is None
        assert "default" in err.lower()


# ===========================================================================
# banks / tags
# ===========================================================================
class TestBanks:
    def test_lists_banks(self):
        svc = _make_service()
        payload = {"banks": [
            {"id": "lore", "name": "Lore", "can_write": True,
             "memory_count": 3},
            {"id": "personal", "name": "Personal", "can_write": False,
             "memory_count": 0},
        ]}
        sess = FakeSession(default=FakeResp(payload=payload))
        assert _run(svc, "/m banks", sess) is True
        # Endpoint + payload correct (bank field ignored -> empty payload).
        assert sess.last_endpoint() == "/bank/list"
        assert sess.last_payload() == {}
        out = _last_text(svc)
        assert "lore" in out and "personal" in out
        assert "rw" in out and "ro" in out

    def test_empty_banks(self):
        svc = _make_service()
        sess = FakeSession(default=FakeResp(payload={"banks": []}))
        assert _run(svc, "/m banks", sess) is True
        assert "don't have access" in _last_text(svc)


class TestTags:
    def test_lists_tags_with_counts(self):
        svc = _make_service(bank_id="lore")
        payload = {"bank": "lore",
                   "tags": [{"tag": "mithril", "count": 4},
                            {"tag": "mines", "count": 2}]}
        sess = FakeSession(default=FakeResp(payload=payload))
        assert _run(svc, "/m tags", sess) is True
        assert sess.last_endpoint() == "/tag/list"
        assert sess.last_payload() == {"bank": "lore"}
        out = _last_text(svc)
        assert "mithril" in out and "4" in out

    def test_uses_explicit_bank(self):
        svc = _make_service(bank_id="personal")
        sess = FakeSession(default=FakeResp(payload={"tags": []}))
        _run(svc, "/m tags worldbuilding", sess)
        assert sess.last_payload() == {"bank": "worldbuilding"}


# ===========================================================================
# add
# ===========================================================================
class TestAdd:
    def test_minimal_add(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"id": "abc", "bank":
                                                     "lore"}))
        assert _run(svc, "/m add .Parking.Section G7", sess) is True
        assert sess.last_endpoint() == "/memory/add"
        p = sess.last_payload()
        assert p["bank"] == "lore"
        assert p["name"] == "Parking"
        assert p["content"] == "Section G7"
        assert "tags" not in p and "timestamp" not in p
        assert "abc" in _last_text(svc)

    def test_add_with_tags(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"id": "abc"}))
        _run(svc, "/m add lore.Name.Body text.mithril, mines", sess)
        assert sess.last_payload()["tags"] == ["mithril", "mines"]

    def test_add_with_timestamp_date(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"id": "abc"}))
        _run(svc, "/m add lore.Name.Body.tagx.2024-01-01", sess)
        p = sess.last_payload()
        assert p["timestamp"] == 1704067200
        assert p["tags"] == ["tagx"]

    def test_add_missing_content_usage_error(self):
        svc = _make_service(bank_id="lore")
        # No session should be needed; usage error is raised before POST.
        assert _run(svc, "/m add lore.OnlyName") is False
        assert "Usage" in _last_text(svc)

    def test_add_bad_timestamp(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m add lore.N.C.t.notadate") is False
        assert "timestamp" in _last_text(svc).lower()


# ===========================================================================
# get
# ===========================================================================
class TestGet:
    def test_renders_full_memory(self):
        svc = _make_service(bank_id="lore")
        mem = {"id": "abc", "name": "Mithril", "content": "A rare metal.",
               "tags": ["mithril", "mines"], "timestamp": 1704067200}
        sess = FakeSession(default=FakeResp(payload={"memory": mem}))
        assert _run(svc, "/m get lore.abc", sess) is True
        assert sess.last_endpoint() == "/memory/get"
        assert sess.last_payload() == {"bank": "lore", "id": "abc"}
        out = _last_text(svc)
        assert "Mithril" in out
        assert "abc" in out
        assert "mithril" in out
        assert "A rare metal." in out

    def test_missing_id_usage(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m get lore") is False
        assert "Usage" in _last_text(svc)

    def test_escapes_html(self):
        svc = _make_service(bank_id="lore")
        mem = {"id": "x", "name": "<b>x</b>", "content": "a<b>c",
               "tags": [], "timestamp": 1704067200}
        sess = FakeSession(default=FakeResp(payload={"memory": mem}))
        _run(svc, "/m get lore.x", sess)
        out = _last_text(svc)
        assert "&lt;b&gt;" in out
        # The raw injected markup must be escaped.
        assert "a&lt;b&gt;c" in out


# ===========================================================================
# search
# ===========================================================================
class TestSearchPayload:
    def test_all_filters(self):
        payload, err = memory._build_search_payload("lore", [
            "kw=mithril", "tags=a,b", "mode=all",
            "from=2024-01-01", "to=2024-01-02",
            "limit=5", "offset=10", "order=asc",
        ])
        assert err is None
        assert payload["bank"] == "lore"
        assert payload["filters"]["keyword"] == "mithril"
        assert payload["filters"]["tags"] == ["a", "b"]
        assert payload["filters"]["tag_mode"] == "all"
        assert payload["filters"]["time_range"] == {
            "start": 1704067200, "end": 1704153600}
        assert payload["limit"] == 5
        assert payload["offset"] == 10
        assert payload["order"] == "asc"

    def test_limit_capped(self):
        payload, err = memory._build_search_payload("lore", ["limit=9000"])
        assert err is None
        assert payload["limit"] == memory.SEARCH_LIMIT_CAP

    def test_bad_mode(self):
        _, err = memory._build_search_payload("lore", ["mode=maybe"])
        assert err is not None

    def test_bad_order(self):
        _, err = memory._build_search_payload("lore", ["order=sideways"])
        assert err is not None

    def test_unknown_filter(self):
        _, err = memory._build_search_payload("lore", ["color=blue"])
        assert err is not None

    def test_bad_date(self):
        _, err = memory._build_search_payload("lore", ["from=whenever"])
        assert err is not None

    def test_non_kv_field(self):
        _, err = memory._build_search_payload("lore", ["justtext"])
        assert err is not None

    def test_empty_filters_no_filters_key(self):
        payload, err = memory._build_search_payload("lore", [])
        assert err is None
        assert "filters" not in payload


class TestSearchCommand:
    def test_whitespace_tolerant_parsing(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={
            "count": 0, "total": 0, "memories": []}))
        # Whitespace around . and = must parse identically to the compact form.
        _run(svc, "/m search lore . kw = mithril . limit = 5", sess)
        p = sess.last_payload()
        assert p["filters"]["keyword"] == "mithril"
        assert p["limit"] == 5
        assert sess.last_endpoint() == "/memory/list"

    def test_renders_compact_list(self):
        svc = _make_service(bank_id="lore")
        payload = {"count": 2, "total": 2, "memories": [
            {"id": "1", "name": "One", "content": "first content",
             "tags": ["a"]},
            {"id": "2", "name": "Two", "content": "second content",
             "tags": []},
        ]}
        sess = FakeSession(default=FakeResp(payload=payload))
        _run(svc, "/m search lore.kw=x", sess)
        out = _last_text(svc)
        assert "One" in out and "Two" in out
        assert "#a" in out

    def test_truncation_note(self):
        svc = _make_service(bank_id="lore")
        payload = {"count": 2, "total": 10, "memories": [
            {"id": "1", "name": "One", "content": "c", "tags": []},
            {"id": "2", "name": "Two", "content": "c", "tags": []},
        ]}
        sess = FakeSession(default=FakeResp(payload=payload))
        _run(svc, "/m search lore.limit=2", sess)
        out = _last_text(svc)
        assert "truncated" in out.lower()

    def test_no_matches(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={
            "count": 0, "total": 0, "memories": []}))
        _run(svc, "/m search lore.kw=nope", sess)
        assert "matched" in _last_text(svc).lower()

    def test_bad_filter_no_post(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession()
        assert _run(svc, "/m search lore.mode=bogus", sess) is False
        assert len(sess.calls) == 0


# ===========================================================================
# edit
# ===========================================================================
class TestEdit:
    def test_partial_update_single_field(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"id": "abc",
                                                     "updated": True}))
        assert _run(svc, "/m edit lore.abc.name=New Name", sess) is True
        assert sess.last_endpoint() == "/memory/update"
        p = sess.last_payload()
        assert p == {"bank": "lore", "id": "abc", "name": "New Name"}
        # content/tags must NOT be present (only provided fields change).
        assert "content" not in p and "tags" not in p

    def test_multiple_fields_and_tags(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"updated": True}))
        _run(svc, "/m edit lore.abc.content=Body.tags=a, b", sess)
        p = sess.last_payload()
        assert p["content"] == "Body"
        assert p["tags"] == ["a", "b"]

    def test_missing_id_usage(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m edit lore") is False
        assert "Usage" in _last_text(svc)

    def test_no_updates_usage(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m edit lore.abc") is False
        assert "Usage" in _last_text(svc)

    def test_unknown_field(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m edit lore.abc.color=blue") is False
        assert "color" in _last_text(svc)


# ===========================================================================
# del / rebuild
# ===========================================================================
class TestDelete:
    def test_delete(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"id": "abc",
                                                     "deleted": True}))
        assert _run(svc, "/m del lore.abc", sess) is True
        assert sess.last_endpoint() == "/memory/delete"
        assert sess.last_payload() == {"bank": "lore", "id": "abc"}

    def test_missing_id_usage(self):
        svc = _make_service(bank_id="lore")
        assert _run(svc, "/m del lore") is False
        assert "Usage" in _last_text(svc)


class TestRebuild:
    def test_rebuild(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(payload={"bank": "lore",
                                                     "rebuilt": True}))
        assert _run(svc, "/m rebuild lore", sess) is True
        assert sess.last_endpoint() == "/bank/rebuild_tags"
        assert sess.last_payload() == {"bank": "lore"}

    def test_rebuild_forbidden(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(
            success=False, status=403, message="nope"))
        assert _run(svc, "/m rebuild lore", sess) is False
        assert "permission" in _last_text(svc).lower()


# ===========================================================================
# Default-bank resolution through a command
# ===========================================================================
class TestDefaultBankThroughCommand:
    def test_default_used_when_bank_empty(self):
        svc = _make_service(bank_id="personal")
        sess = FakeSession(default=FakeResp(payload={"tags": []}))
        _run(svc, "/m tags", sess)
        assert sess.last_payload() == {"bank": "personal"}

    def test_no_default_errors_before_post(self):
        svc = _make_service(bank_id=None)
        sess = FakeSession()
        assert _run(svc, "/m tags", sess) is False
        assert len(sess.calls) == 0
        assert "default" in _last_text(svc).lower()


# ===========================================================================
# help / unknown
# ===========================================================================
class TestHelpAndUnknown:
    def test_bare_command_shows_help(self):
        svc = _make_service(bank_id="personal")
        assert _run(svc, "/m") is True
        out = _last_text(svc)
        assert "Subcommands" in out
        assert "search" in out
        assert "YYYY-MM-DD" in out  # documents accepted date formats

    def test_help_keyword(self):
        svc = _make_service(bank_id="personal")
        assert _run(svc, "/m help") is True
        assert "Subcommands" in _last_text(svc)

    def test_help_notes_default_bank(self):
        svc = _make_service(bank_id="personal")
        _run(svc, "/m help")
        assert "personal" in _last_text(svc)

    def test_help_notes_missing_default(self):
        svc = _make_service(bank_id=None)
        _run(svc, "/m help")
        assert "no default bank" in _last_text(svc).lower()

    def test_unknown_subcommand(self):
        svc = _make_service(bank_id="personal")
        assert _run(svc, "/m frobnicate x") is False
        # Two messages: the error + help.
        texts = [c[0][1] for c in svc.send_message.call_args_list]
        assert any("Unknown subcommand" in t for t in texts)


# ===========================================================================
# ACL / error surfacing
# ===========================================================================
class TestErrorSurfacing:
    def test_404_not_found(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(
            success=False, status=404, message="File not found."))
        assert _run(svc, "/m get lore.missing", sess) is False
        assert "find" in _last_text(svc).lower()

    def test_403_read_only(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(
            success=False, status=403, message="forbidden"))
        assert _run(svc, "/m del lore.abc", sess) is False
        assert "permission" in _last_text(svc).lower()

    def test_400_invalid_tag(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(
            success=False, status=400,
            message="Tags must not be empty."))
        assert _run(svc, "/m add lore.N.C.,,,", sess) is False
        out = _last_text(svc)
        assert "Tags must not be empty." in out

    def test_503_busy(self):
        svc = _make_service(bank_id="lore")
        sess = FakeSession(default=FakeResp(
            success=False, status=503, message="busy"))
        assert _run(svc, "/m tags lore", sess) is False
        assert "busy" in _last_text(svc).lower()


# ===========================================================================
# Membank session helper
# ===========================================================================
class TestGetMembankSession:
    def test_missing_config_reports(self):
        svc = types.SimpleNamespace()
        svc.config = types.SimpleNamespace(membank=None)
        svc.send_message = MagicMock()
        msg = _make_message()
        assert memory._get_membank_session(svc, msg) is None
        assert "not configured" in svc.send_message.call_args[0][1].lower()

    def test_login_failure_reports(self):
        svc = types.SimpleNamespace()
        svc.config = types.SimpleNamespace(membank=object())
        svc.send_message = MagicMock()
        msg = _make_message()

        fake_session = MagicMock()
        fake_session.login.return_value = FakeResp(success=False, status=401)
        fake_session.get_response_success = lambda r: r.success
        with patch.object(memory, "OracleSession",
                          return_value=fake_session):
            assert memory._get_membank_session(svc, msg) is None
        assert "authenticate" in svc.send_message.call_args[0][1].lower()

    def test_login_success_returns_session(self):
        svc = types.SimpleNamespace()
        svc.config = types.SimpleNamespace(membank=object())
        svc.send_message = MagicMock()
        msg = _make_message()

        fake_session = MagicMock()
        fake_session.login.return_value = FakeResp(success=True, status=200)
        fake_session.get_response_success = lambda r: r.success
        with patch.object(memory, "OracleSession",
                          return_value=fake_session):
            out = memory._get_membank_session(svc, msg)
        assert out is fake_session


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
