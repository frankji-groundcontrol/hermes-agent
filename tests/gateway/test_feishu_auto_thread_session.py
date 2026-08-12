"""Feishu top-level mention topic routing and session continuity."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType, _thread_metadata_for_source
from gateway.session import (
    SessionSource,
    SessionStore,
    build_session_key,
    is_shared_multi_user_session,
)


def _provisional() -> SessionSource:
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="group",
        reply_thread_anchor_id="om_top",
        reply_thread_strict=True,
    )


def _topic() -> SessionSource:
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="group",
        thread_id="omt_topic",
    )


def test_provisional_lane_is_unique_per_top_level_message():
    first = _provisional()
    second = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="group",
        reply_thread_anchor_id="om_other",
        reply_thread_strict=True,
    )
    assert build_session_key(first) != build_session_key(second)
    assert "omt_" not in build_session_key(first)
    assert is_shared_multi_user_session(first)


def test_bind_session_alias_preserves_session_and_survives_reload(tmp_path):
    config = GatewayConfig()
    store = SessionStore(sessions_dir=tmp_path, config=config)
    provisional = store.get_or_create_session(_provisional())

    alias = store.bind_session_alias(_topic(), _provisional())
    assert alias is provisional
    assert alias.session_id == provisional.session_id
    assert alias.session_key == store._generate_session_key(_topic())
    routing = json.loads((tmp_path / "sessions.json").read_text())
    assert store._generate_session_key(_provisional()) not in routing
    assert store.get_or_create_session(_provisional()) is alias
    assert store.get_or_create_session(_topic()).session_id == provisional.session_id

    reloaded = SessionStore(sessions_dir=tmp_path, config=config)
    assert reloaded.get_or_create_session(_topic()).session_id == provisional.session_id


def test_rekey_preserves_and_clears_first_turn_marker(tmp_path):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())
    provisional_key = provisional.session_key
    token = store.mark_turn_active(provisional_key)

    canonical = store.bind_session_alias(_topic(), _provisional())
    assert canonical is provisional
    assert canonical.active_turn_token == token
    assert store.clear_turn_active(provisional_key, token) is True

    reloaded = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    assert reloaded.recover_interrupted_turns() == 0
    assert reloaded.get_or_create_session(_topic()).active_turn_token is None


def test_stale_turn_clear_keeps_redirect_owned_by_newer_turn(tmp_path):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())
    provisional_key = provisional.session_key
    stale_token = store.mark_turn_active(provisional_key)
    canonical = store.bind_session_alias(_topic(), _provisional())
    current_token = store.mark_turn_active(provisional_key)

    assert store.clear_turn_active(provisional_key, stale_token) is False
    assert store.get_or_create_session(_provisional()) is canonical
    assert store.clear_turn_active(provisional_key, current_token) is True


def test_bind_session_alias_refuses_to_overwrite_other_topic_owner(tmp_path):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())
    topic = store.get_or_create_session(_topic())
    assert topic.session_id != provisional.session_id
    assert store.bind_session_alias(_topic(), _provisional()) is None
    assert store.get_or_create_session(_topic()).session_id == topic.session_id


def test_rekey_keeps_live_route_when_persistence_fails(tmp_path):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())

    with patch.object(store, "_persist_routing_data", side_effect=OSError("disk")):
        with pytest.raises(OSError, match="disk"):
            store.bind_session_alias(_topic(), _provisional())

    assert store.get_or_create_session(_topic()) is provisional
    assert list(store._entries) == [store._generate_session_key(_topic())]


@pytest.mark.parametrize("failed_store", ["db", "json"])
def test_rekey_partial_persistence_reloads_only_canonical_route(
    tmp_path, failed_store
):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())
    provisional_key = provisional.session_key
    canonical_key = store._generate_session_key(_topic())

    target = (
        store._db
        if failed_store == "db"
        else store
    )
    method = (
        "replace_gateway_routing_entries"
        if failed_store == "db"
        else "_save_sessions_json"
    )
    with patch.object(target, method, side_effect=OSError("injected")):
        assert store.bind_session_alias(_topic(), _provisional()) is provisional

    reloaded = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    reloaded._ensure_loaded()
    assert list(reloaded._entries) == [canonical_key]
    assert reloaded._entries[canonical_key].session_id == provisional.session_id
    assert provisional_key not in reloaded._entries

    reloaded_again = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    reloaded_again._ensure_loaded()
    assert list(reloaded_again._entries) == [canonical_key]


@pytest.mark.parametrize("failed_store", ["db", "json"])
def test_runtime_recovery_preserves_partial_rekey_tombstone(tmp_path, failed_store):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    provisional = store.get_or_create_session(_provisional())
    provisional_key = provisional.session_key
    canonical_key = store._generate_session_key(_topic())
    target = store._db if failed_store == "db" else store
    method = (
        "replace_gateway_routing_entries"
        if failed_store == "db"
        else "_save_sessions_json"
    )
    with patch.object(target, method, side_effect=OSError("injected")):
        store.bind_session_alias(_topic(), _provisional())

    store._db.end_session(provisional.session_id, "agent_close")
    recovered = store.get_or_create_session(_topic())
    assert recovered.session_id == provisional.session_id

    reloaded = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    reloaded._ensure_loaded()
    assert list(reloaded._entries) == [canonical_key]
    assert provisional_key not in reloaded._entries


def test_bootstrap_alias_keeps_profile_identity_and_active_guard(tmp_path):
    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    config = GatewayConfig(
        multiplex_profiles=True,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    store = SessionStore(
        sessions_dir=tmp_path,
        config=config,
    )
    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="channel",
        user_id="ou_user",
        user_id_alt="on_user",
        profile="secondary",
        reply_thread_anchor_id="om_top",
        reply_thread_strict=True,
    )
    provisional = store.get_or_create_session(source)
    adapter = FeishuAdapter(
        PlatformConfig(
            extra={
                "group_sessions_per_user": True,
                "thread_sessions_per_user": True,
            }
        )
    )
    adapter._session_store = store
    provisional_key = build_session_key(
        source,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    adapter._active_sessions[provisional_key] = asyncio.Event()
    adapter._bind_bootstrap_thread_session(
        _thread_metadata_for_source(source), "omt_topic"
    )

    assert source.thread_id == "omt_topic"
    assert store.get_or_create_session(source).session_id == provisional.session_id
    reloaded = SessionStore(sessions_dir=tmp_path, config=config)
    assert reloaded.get_or_create_session(source).session_id == provisional.session_id

    async def _unused_handler(_event):
        return None

    adapter._message_handler = _unused_handler
    follow_up_source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="channel",
        user_id="ou_user",
        user_id_alt="on_user",
        profile="secondary",
        thread_id="omt_topic",
    )
    follow_up = MessageEvent(
        text="follow up",
        message_type=MessageType.TEXT,
        source=follow_up_source,
        message_id="om_follow_up",
    )
    asyncio.run(adapter.handle_message(follow_up))
    assert getattr(follow_up_source, "_gateway_adapter_session_key") == provisional_key
    assert adapter._pending_messages[provisional_key] is follow_up


def test_runner_uses_profiled_provisional_key_but_adapter_queue_key(tmp_path):
    from gateway.run import GatewayRunner

    config = GatewayConfig(
        multiplex_profiles=True,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    store = SessionStore(sessions_dir=tmp_path, config=config)
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = config
    runner.session_store = store
    runner._sessions = {}
    runner.adapters = {}

    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_chat",
        chat_type="channel",
        user_id_alt="on_user",
        profile="secondary",
        thread_id="omt_topic",
    )
    adapter_key = build_session_key(
        _provisional(),
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    setattr(source, "_gateway_adapter_session_key", adapter_key)
    runner_key = runner._session_key_for_source(source)
    assert runner_key.startswith("agent:secondary:")
    assert runner_key.endswith("feishu-auto-om_top")

    adapter = MagicMock()
    adapter.config.extra = {
        "group_sessions_per_user": True,
        "thread_sessions_per_user": True,
    }
    adapter._pending_messages = {}
    runner._adapter_for_source = lambda _source: adapter
    event = MessageEvent(
        text="follow up",
        message_type=MessageType.TEXT,
        source=source,
        message_id="om_follow_up",
    )
    runner._queue_or_replace_pending_event(runner_key, event)

    assert adapter._pending_messages[adapter_key] is event
    assert runner_key not in adapter._pending_messages


@pytest.mark.asyncio
async def test_profiled_bootstrap_queue_uses_adapter_lane(tmp_path):
    from gateway.run import GatewayRunner, _dequeue_pending_event

    config = GatewayConfig(
        multiplex_profiles=True,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = config
    runner.session_store = SessionStore(sessions_dir=tmp_path, config=config)
    runner._sessions = {}

    source = _topic()
    source.profile = "secondary"
    adapter_key = build_session_key(_provisional())
    setattr(source, "_gateway_adapter_session_key", adapter_key)
    runner_key = runner._session_key_for_source(source)
    adapter = MagicMock()
    adapter._pending_messages = {}
    adapter.get_pending_message.side_effect = adapter._pending_messages.pop
    runner._adapter_for_source = lambda _source: adapter
    event = MessageEvent(
        text="/queue follow up",
        message_type=MessageType.TEXT,
        source=source,
        message_id="om_queue",
    )

    assert await runner._busy_queue_command(event, runner_key, source) == (
        "Queued for the next turn."
    )
    assert _dequeue_pending_event(adapter, adapter_key).text == "follow up"
    assert runner_key not in adapter._pending_messages
