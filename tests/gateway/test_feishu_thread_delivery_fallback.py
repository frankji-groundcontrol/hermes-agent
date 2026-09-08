"""Live-gap regression: Feishu thread_id receive rejection (99992402).

A/B-tested live 2026-09-08: the public send API rejects
``receive_id_type=thread_id`` with 99992402 (field validation failed), while
replying to an in-topic message routes into the topic. Strict thread delivery
must fall back to the Reply API and never leak a flat chat send.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from plugins.platforms.feishu.adapter import FeishuAdapter


def _adapter(create_response, reply_response=None, last_in_thread=None):
    adapter = MagicMock(spec=FeishuAdapter)
    client = MagicMock()
    client.im.v1.message.create = MagicMock(return_value=create_response)
    if reply_response is not None:
        client.im.v1.message.reply = MagicMock(return_value=reply_response)
    adapter._client = client
    adapter._build_create_message_body = FeishuAdapter._build_create_message_body
    adapter._build_create_message_request = FeishuAdapter._build_create_message_request
    adapter._build_reply_message_body = FeishuAdapter._build_reply_message_body
    adapter._build_reply_message_request = FeishuAdapter._build_reply_message_request
    adapter._response_succeeded = FeishuAdapter._response_succeeded

    async def _run_blocking_passthrough(func, *args):
        return func(*args)

    adapter._run_blocking = _run_blocking_passthrough

    async def _fetch_last(thread_id):
        return last_in_thread

    adapter._fetch_last_message_in_thread = _fetch_last
    return adapter, client


def _rejected():
    return SimpleNamespace(success=lambda: False, code=99992402, msg="field validation failed")


def _ok(mid):
    return SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(message_id=mid, thread_id="omt_topic_abc"),
    )


async def _send(adapter):
    return await FeishuAdapter._send_raw_message(
        adapter,
        chat_id="oc_main_chat",
        msg_type="text",
        payload=json.dumps({"text": "hello"}),
        reply_to=None,
        metadata={"thread_id": "omt_topic_abc"},
    )


class TestThreadReceiveFallback:
    @pytest.mark.asyncio
    async def test_rejection_falls_back_to_reply_in_thread(self):
        adapter, client = _adapter(
            _rejected(), reply_response=_ok("reply_msg_1"), last_in_thread="om_anchor_1"
        )
        result = await _send(adapter)
        client.im.v1.message.create.assert_called_once()
        client.im.v1.message.reply.assert_called_once()
        assert getattr(result.data, "message_id", None) == "reply_msg_1"

    @pytest.mark.asyncio
    async def test_rejection_without_anchor_fails_closed(self):
        adapter, client = _adapter(_rejected(), reply_response=_ok("unused"), last_in_thread=None)
        result = await _send(adapter)
        client.im.v1.message.reply.assert_not_called()
        # exactly the one rejected thread-create; no flat chat retry
        assert client.im.v1.message.create.call_count == 1
        assert getattr(result, "code", None) == 99992402

    @pytest.mark.asyncio
    async def test_other_failures_pass_through_without_fallback(self):
        other = SimpleNamespace(success=lambda: False, code=230001, msg="other error")
        adapter, client = _adapter(other, reply_response=_ok("unused"), last_in_thread="om_anchor_1")
        result = await _send(adapter)
        client.im.v1.message.reply.assert_not_called()
        assert getattr(result, "code", None) == 230001

    @pytest.mark.asyncio
    async def test_success_returns_without_fallback(self):
        adapter, client = _adapter(_ok("created_1"), reply_response=_ok("unused"), last_in_thread=None)
        result = await _send(adapter)
        client.im.v1.message.reply.assert_not_called()
        assert getattr(result.data, "message_id", None) == "created_1"
