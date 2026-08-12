"""Room-scoped Feishu authorization: parser, adapter bypass, and gateway bypass.

Covers ``gateway.feishu_authorization`` (the shared strict parser/helper), the
``FeishuAdapter._admit`` / ``_allow_group_message`` listed-room bypass, and the
``GatewayRunner._is_user_authorized`` listed-room bypass.

All identifiers are synthetic (``oc_listed``, ``oc_unlisted``, ``ou_unknown``,
``ou_bot``, ``om_sanitized``). No real Feishu chat/user/message IDs appear.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from tests.gateway.feishu_helpers import (
    make_adapter_skeleton,
    make_message,
    make_sender,
    stub_mention,
)


# ---------------------------------------------------------------------------
# Shared synthetic values (no real IDs)
# ---------------------------------------------------------------------------

LISTED_ROOM = "oc_listed"
UNLISTED_ROOM = "oc_unlisted"
UNKNOWN_HUMAN = "ou_unknown"
BOT_OPEN_ID = "ou_bot"


def _set_listed(monkeypatch, *extra: str) -> None:
    monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", ",".join((LISTED_ROOM, *extra)))


# ===========================================================================
# Step 1/3: parser — parse_feishu_group_allowed_chats / is_feishu_group_chat_allowed
# ===========================================================================


class TestParsing:
    """Strict parser: only valid ``oc_*`` comma-separated IDs are admitted."""

    def test_parsing_none_returns_empty(self, monkeypatch):
        monkeypatch.delenv("FEISHU_GROUP_ALLOWED_CHATS", raising=False)
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_empty_returns_empty(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", "")
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_blank_returns_empty(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", "   ")
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_single_valid_id(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", LISTED_ROOM)
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset({LISTED_ROOM})

    def test_parsing_multiple_comma_separated_ids(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", f"{LISTED_ROOM},oc_other")
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset({LISTED_ROOM, "oc_other"})

    def test_parsing_normalizes_whitespace(self, monkeypatch):
        monkeypatch.setenv(
            "FEISHU_GROUP_ALLOWED_CHATS", f"  {LISTED_ROOM} ,  oc_other  "
        )
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset({LISTED_ROOM, "oc_other"})

    def test_parsing_dedupes(self, monkeypatch):
        monkeypatch.setenv(
            "FEISHU_GROUP_ALLOWED_CHATS", f"{LISTED_ROOM},{LISTED_ROOM}"
        )
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset({LISTED_ROOM})

    def test_parsing_rejects_empty_entry(self, monkeypatch):
        # "oc_listed,,oc_other" has an empty entry between two valid IDs.
        monkeypatch.setenv(
            "FEISHU_GROUP_ALLOWED_CHATS", f"{LISTED_ROOM},,oc_other"
        )
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_rejects_non_chat_id(self, monkeypatch):
        # ou_* is a user ID, not a chat ID. Whole list is rejected.
        monkeypatch.setenv(
            "FEISHU_GROUP_ALLOWED_CHATS", f"{LISTED_ROOM},ou_not_a_chat"
        )
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_rejects_newline_separator(self, monkeypatch):
        # Newline-separated values are not supported; only comma lists are.
        monkeypatch.setenv(
            "FEISHU_GROUP_ALLOWED_CHATS", f"{LISTED_ROOM}\noc_other"
        )
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats() == frozenset()

    def test_parsing_explicit_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", LISTED_ROOM)
        from gateway.feishu_authorization import parse_feishu_group_allowed_chats

        assert parse_feishu_group_allowed_chats("oc_explicit") == frozenset(
            {"oc_explicit"}
        )


class TestNormalizesAndMembership:
    def test_is_feishu_group_chat_allowed_true_for_listed(self, monkeypatch):
        _set_listed(monkeypatch)
        from gateway.feishu_authorization import is_feishu_group_chat_allowed

        assert is_feishu_group_chat_allowed(LISTED_ROOM) is True

    def test_is_feishu_group_chat_allowed_false_for_unlisted(self, monkeypatch):
        _set_listed(monkeypatch)
        from gateway.feishu_authorization import is_feishu_group_chat_allowed

        assert is_feishu_group_chat_allowed(UNLISTED_ROOM) is False

    def test_is_feishu_group_chat_allowed_false_for_missing(self, monkeypatch):
        _set_listed(monkeypatch)
        from gateway.feishu_authorization import is_feishu_group_chat_allowed

        assert is_feishu_group_chat_allowed(None) is False
        assert is_feishu_group_chat_allowed("") is False


# ===========================================================================
# Step 4/6: adapter truth table — FeishuAdapter._admit listed-room bypass
# ===========================================================================


@pytest.fixture(autouse=True)
def _isolate_feishu_env(monkeypatch):
    """Blank the Feishu auth env surface so each test sets only what it needs."""
    for var in (
        "FEISHU_GROUP_ALLOWED_CHATS",
        "FEISHU_GROUP_POLICY",
        "FEISHU_ALLOW_BOTS",
        "FEISHU_ALLOWED_USERS",
        "FEISHU_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS",
        "FEISHU_REQUIRE_MENTION",
    ):
        monkeypatch.delenv(var, raising=False)


def _admit_group(adapter, *, chat_id: str, open_id: str = UNKNOWN_HUMAN,
                 sender_type: str = "user", mentions_self: bool = True):
    sender = make_sender(sender_type, open_id=open_id)
    message = make_message(chat_type="group", chat_id=chat_id)
    stub_mention(adapter, mentions_self)
    return adapter._admit(sender, message)


class TestAdapterTruthTable:
    def test_profile_yaml_bot_policy_does_not_borrow_process_env(self, monkeypatch):
        from agent import secret_scope
        from plugins.platforms.feishu.adapter import FeishuAdapter, _apply_yaml_config

        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)
        first_extra = _apply_yaml_config({}, {"allow_bots": "all"})
        assert os.environ["FEISHU_ALLOW_BOTS"] == "all"

        monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
        token = secret_scope.set_secret_scope({})
        try:
            second_extra = _apply_yaml_config({}, {"allow_bots": "none"})
            second_settings = FeishuAdapter._load_settings(second_extra)
        finally:
            secret_scope.reset_secret_scope(token)

        assert second_extra == {"allow_bots": "none"}
        assert second_settings.allow_bots == "none"
        assert os.environ["FEISHU_ALLOW_BOTS"] == "all"

    def test_load_settings_uses_profile_scoped_authorization(self, monkeypatch):
        from agent.secret_scope import reset_secret_scope, set_secret_scope
        from plugins.platforms.feishu.adapter import FeishuAdapter

        monkeypatch.setenv("FEISHU_ALLOW_BOTS", "all")
        monkeypatch.setenv("FEISHU_GROUP_POLICY", "open")
        monkeypatch.setenv("FEISHU_ALLOWED_USERS", "ou_other_profile")
        token = set_secret_scope({
            "FEISHU_ALLOW_BOTS": "none",
            "FEISHU_GROUP_POLICY": "disabled",
            "FEISHU_ALLOWED_USERS": "ou_this_profile",
            "FEISHU_REQUIRE_MENTION": "false",
            "FEISHU_ALLOW_ALL_USERS": "false",
            "GATEWAY_ALLOW_ALL_USERS": "false",
        })
        try:
            settings = FeishuAdapter._load_settings({})
        finally:
            reset_secret_scope(token)

        assert settings.allow_bots == "none"
        assert settings.group_policy == "disabled"
        assert settings.allowed_group_users == frozenset({"ou_this_profile"})
        assert settings.require_mention is False
        assert settings.allow_all_users is False
        assert settings.gateway_allow_all_users is False

    def test_listed_group_unknown_human_with_mention_admitted(self, monkeypatch):
        _set_listed(monkeypatch)
        adapter = make_adapter_skeleton(group_policy="allowlist")
        assert _admit_group(adapter, chat_id=LISTED_ROOM, mentions_self=True) is None

    def test_unlisted_room_falls_through_to_existing_policy(self, monkeypatch):
        # allowlist policy + empty allowed_group_users -> deny unlisted human.
        adapter = make_adapter_skeleton(group_policy="allowlist")
        assert _admit_group(adapter, chat_id=UNLISTED_ROOM) == "group_policy_rejected"

    def test_missing_room_falls_through_to_existing_policy(self, monkeypatch):
        adapter = make_adapter_skeleton(group_policy="allowlist")
        # Empty chat_id: not listed, not p2p -> group policy reject.
        assert _admit_group(adapter, chat_id="") == "group_policy_rejected"

    def test_listed_room_without_mention_denied(self, monkeypatch):
        _set_listed(monkeypatch)
        adapter = make_adapter_skeleton(group_policy="allowlist", require_mention=True)
        assert _admit_group(adapter, chat_id=LISTED_ROOM, mentions_self=False) == (
            "group_policy_rejected"
        )

    def test_listed_room_policy_disabled_denied(self, monkeypatch):
        _set_listed(monkeypatch)
        monkeypatch.setenv("FEISHU_GROUP_POLICY", "disabled")
        adapter = make_adapter_skeleton(group_policy="disabled")
        assert _admit_group(adapter, chat_id=LISTED_ROOM) == "group_policy_rejected"

    @pytest.mark.parametrize("allow_bots", [None, "none", "unexpected"])
    def test_listed_room_bot_sender_denied_when_allow_bots_not_mentions_or_all(
        self, monkeypatch, allow_bots
    ):
        _set_listed(monkeypatch)
        env_value = "none" if allow_bots is None else allow_bots
        monkeypatch.setenv("FEISHU_ALLOW_BOTS", env_value)
        adapter = make_adapter_skeleton(
            group_policy="allowlist",
            allow_bots=("none" if allow_bots is None else allow_bots),
            bot_open_id=BOT_OPEN_ID,
        )
        # Bot gate in _admit fires before the listed-room bypass.
        assert _admit_group(
            adapter, chat_id=LISTED_ROOM, open_id="ou_peer_bot", sender_type="bot"
        ) == "bots_disabled"

    @pytest.mark.parametrize("allow_bots", ["mentions", "all"])
    def test_listed_room_bot_sender_admitted_when_allow_bots_mentions_or_all(
        self, monkeypatch, allow_bots
    ):
        # "admitted under existing mention semantics": the adapter still
        # enforces require_mention for bot traffic, so satisfy it.
        _set_listed(monkeypatch)
        monkeypatch.setenv("FEISHU_ALLOW_BOTS", allow_bots)
        adapter = make_adapter_skeleton(
            group_policy="allowlist",
            allow_bots=allow_bots,
            bot_open_id=BOT_OPEN_ID,
            require_mention=True,
        )
        assert _admit_group(
            adapter, chat_id=LISTED_ROOM, open_id="ou_peer_bot",
            sender_type="bot", mentions_self=True,
        ) is None

    def test_dm_admission_unchanged_by_listed_room(self, monkeypatch):
        # A DM whose chat_id happens to look listed must not get the group bypass:
        # the listed-room bypass is group/forum only.
        _set_listed(monkeypatch)
        adapter = make_adapter_skeleton(group_policy="allowlist")
        # p2p chat with chat_id == LISTED_ROOM: empty allowed users -> pairing
        # forward (returns None), unchanged DM behavior.
        sender = make_sender("user", open_id=UNKNOWN_HUMAN)
        message = make_message(chat_type="p2p", chat_id=LISTED_ROOM)
        stub_mention(adapter, True)
        assert adapter._admit(sender, message) is None


# ===========================================================================
# Step 7/9: gateway truth table — GatewayRunner._is_user_authorized bypass
# ===========================================================================


def _make_bare_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=lambda *_a, **_kw: False)
    return runner


def _make_feishu_source(
    *,
    chat_id: str = LISTED_ROOM,
    chat_type: str = "group",
    open_id: str = UNKNOWN_HUMAN,
    is_bot: bool = False,
) -> SessionSource:
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=open_id,
        user_name="Human" if not is_bot else "Bot",
        is_bot=is_bot,
    )


class TestGatewayTruthTable:
    def test_adapter_snapshot_wins_over_another_profile_env(self, monkeypatch):
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", UNLISTED_ROOM)
        runner = _make_bare_runner()
        runner.adapters = {
            Platform.FEISHU: SimpleNamespace(
                _allowed_group_chats=frozenset({LISTED_ROOM}),
                _group_policy="allowlist",
                _allow_bots="none",
            )
        }

        assert runner._is_user_authorized(_make_feishu_source()) is True
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=UNLISTED_ROOM)
        ) is False

    def test_listed_human_group_non_disabled_policy_admitted(self, monkeypatch):
        _set_listed(monkeypatch)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(_make_feishu_source()) is True

    def test_listed_human_group_policy_disabled_denied(self, monkeypatch):
        _set_listed(monkeypatch)
        monkeypatch.setenv("FEISHU_GROUP_POLICY", "disabled")
        runner = _make_bare_runner()
        assert runner._is_user_authorized(_make_feishu_source()) is False

    @pytest.mark.parametrize("allow_bots", [None, "none", "unexpected"])
    def test_listed_bot_group_denied_when_allow_bots_not_mentions_or_all(
        self, monkeypatch, allow_bots
    ):
        _set_listed(monkeypatch)
        env_value = "none" if allow_bots is None else allow_bots
        monkeypatch.setenv("FEISHU_ALLOW_BOTS", env_value)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(is_bot=True, open_id="ou_peer_bot")
        ) is False

    @pytest.mark.parametrize("allow_bots", ["mentions", "all"])
    def test_listed_bot_group_admitted_when_allow_bots_mentions_or_all(
        self, monkeypatch, allow_bots
    ):
        _set_listed(monkeypatch)
        monkeypatch.setenv("FEISHU_ALLOW_BOTS", allow_bots)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(is_bot=True, open_id="ou_peer_bot")
        ) is True

    def test_unlisted_human_group_denied(self, monkeypatch):
        _set_listed(monkeypatch)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=UNLISTED_ROOM)
        ) is False

    def test_missing_chat_id_denied(self, monkeypatch):
        _set_listed(monkeypatch)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(_make_feishu_source(chat_id="")) is False

    def test_dm_with_listed_looking_chat_id_denied(self, monkeypatch):
        # DM isolation unchanged: listed-room bypass is group/forum only.
        _set_listed(monkeypatch)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=LISTED_ROOM, chat_type="dm")
        ) is False

    def test_existing_feishu_user_allowlist_still_admits(self, monkeypatch):
        # An unlisted room with the sender in FEISHU_ALLOWED_USERS is admitted
        # by the existing env-allowlist path (not the room bypass).
        monkeypatch.setenv("FEISHU_ALLOWED_USERS", UNKNOWN_HUMAN)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=UNLISTED_ROOM, open_id=UNKNOWN_HUMAN)
        ) is True

    def test_allow_all_users_still_admits_unlisted(self, monkeypatch):
        monkeypatch.setenv("FEISHU_ALLOW_ALL_USERS", "true")
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=UNLISTED_ROOM)
        ) is True

    def test_global_allowed_users_still_admits(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_ALLOWED_USERS", UNKNOWN_HUMAN)
        runner = _make_bare_runner()
        assert runner._is_user_authorized(
            _make_feishu_source(chat_id=UNLISTED_ROOM, open_id=UNKNOWN_HUMAN)
        ) is True

    def test_telegram_isolation_preserved(self, monkeypatch):
        # Telegram has no Feishu room bypass; an unlisted Telegram group with
        # no allowlist is denied.
        _set_listed(monkeypatch)
        runner = _make_bare_runner()
        src = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id=LISTED_ROOM,
            chat_type="group",
            user_id="tg_unknown",
        )
        assert runner._is_user_authorized(src) is False


# ===========================================================================
# Step 13: sanitized adapter-to-runner component tests
# ===========================================================================


class TestAdapterToRunnerComponent:
    """Drive adapter admission -> source construction -> gateway auth.

    Listed event reaches the post-auth sentinel; unlisted event does not.
    """

    @staticmethod
    def _adapter_with_platform():
        adapter = make_adapter_skeleton(group_policy="allowlist")
        adapter.platform = Platform.FEISHU
        return adapter

    def test_listed_event_reaches_post_auth_sentinel(self, monkeypatch):
        _set_listed(monkeypatch)
        adapter = self._adapter_with_platform()
        sender = make_sender("user", open_id=UNKNOWN_HUMAN)
        message = make_message(chat_type="group", chat_id=LISTED_ROOM)
        stub_mention(adapter, True)

        # Stage 1: adapter admission
        assert adapter._admit(sender, message) is None

        # Stage 2: source construction (mirrors _process_inbound_message)
        source = adapter.build_source(
            chat_id=LISTED_ROOM,
            chat_name="Listed Room",
            chat_type="group",
            user_id=UNKNOWN_HUMAN,
            user_name="Human",
            is_bot=False,
        )

        # Stage 3: gateway auth — post-auth sentinel
        runner = _make_bare_runner()
        assert runner._is_user_authorized(source) is True

    def test_unlisted_event_does_not_reach_post_auth_sentinel(self, monkeypatch):
        _set_listed(monkeypatch)
        adapter = self._adapter_with_platform()
        sender = make_sender("user", open_id=UNKNOWN_HUMAN)
        message = make_message(chat_type="group", chat_id=UNLISTED_ROOM)
        stub_mention(adapter, True)

        # Stage 1: adapter rejects the unlisted room at intake.
        assert adapter._admit(sender, message) == "group_policy_rejected"

        # Stage 2: even if a source were constructed, gateway auth denies it.
        source = adapter.build_source(
            chat_id=UNLISTED_ROOM,
            chat_name="Unlisted Room",
            chat_type="group",
            user_id=UNKNOWN_HUMAN,
            user_name="Human",
            is_bot=False,
        )
        runner = _make_bare_runner()
        assert runner._is_user_authorized(source) is False
