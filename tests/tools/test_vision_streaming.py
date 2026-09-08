"""Opt-in streaming auxiliary.vision calls (client-side aggregation).

Regression coverage for the fork patch: ``auxiliary.vision.stream`` gates a
streaming chat-completions call that aggregates deltas client-side, falling
back to the shared non-streaming router call on any miss — required for
gateways whose non-streaming bridge is broken (sub2api #1493/#1552/#5323).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tools.vision_tools as vt

FULL_CFG = {
    "stream": True,
    "base_url": "http://gateway.test/v1",
    "api_key": "test-key",
    "model": "vision-model",
}


def _chunk(text=None, reasoning=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, reasoning_content=reasoning)
            )
        ]
    )


class _FakeCreate:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        items = list(self.chunks)

        class _Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not items:
                    raise StopAsyncIteration
                return items.pop(0)

        return _Stream()


def _install_fake_openai(monkeypatch, chunks):
    import openai

    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        create = _FakeCreate(chunks)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", fake_client)
    return captured


class TestStreamedVisionCompletion:
    @pytest.mark.asyncio
    async def test_gate_closed_returns_none(self):
        assert await vt._streamed_vision_completion(
            [], vision_cfg={}, timeout=1, temperature=0.1
        ) is None

    @pytest.mark.asyncio
    async def test_gate_open_but_incomplete_config_returns_none(self):
        assert await vt._streamed_vision_completion(
            [],
            vision_cfg={"stream": True, "base_url": "http://x"},
            timeout=1,
            temperature=0.1,
        ) is None

    @pytest.mark.asyncio
    async def test_aggregates_content_and_reasoning(self, monkeypatch):
        captured = _install_fake_openai(
            monkeypatch,
            [
                _chunk(reasoning="thinking"),
                _chunk(text="a red "),
                SimpleNamespace(choices=[]),  # keep-alive chunk
                _chunk(text="box"),
            ],
        )
        resp = await vt._streamed_vision_completion(
            [{"role": "user", "content": "describe"}],
            vision_cfg=FULL_CFG,
            timeout=9,
            temperature=0.2,
        )
        assert resp is not None
        assert resp.choices[0].message.content == "a red box"
        assert resp.choices[0].message.reasoning_content == "thinking"
        assert captured["base_url"] == FULL_CFG["base_url"]
        assert captured["api_key"] == FULL_CFG["api_key"]

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_config(self, monkeypatch):
        import openai

        seen = {}

        class _Rec:
            async def __call__(self, **kwargs):
                seen.update(kwargs)

                class _S:
                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        raise StopAsyncIteration

                return _S()

        monkeypatch.setattr(
            openai,
            "AsyncOpenAI",
            lambda **kw: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=_Rec()))
            ),
        )
        resp = await vt._streamed_vision_completion(
            [],
            vision_cfg=FULL_CFG,
            timeout=1,
            temperature=0.1,
            model="override-model",
        )
        assert resp is not None
        assert resp.choices[0].message.content == ""
        assert seen["model"] == "override-model"
        assert seen["stream"] is True

    @pytest.mark.asyncio
    async def test_failure_returns_none_not_raise(self, monkeypatch):
        import openai

        def boom(**kwargs):
            raise RuntimeError("no streaming here")

        monkeypatch.setattr(openai, "AsyncOpenAI", boom)
        assert await vt._streamed_vision_completion(
            [], vision_cfg=FULL_CFG, timeout=1, temperature=0.1
        ) is None


class TestVisionAnalyzeToolStreamingIntegration:
    @staticmethod
    def _data_url():
        import base64

        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 32).decode("ascii")
        return f"data:image/jpeg;base64,{jpeg}"

    @pytest.mark.asyncio
    async def test_streamed_response_used_router_untouched(self):
        streamed = MagicMock()
        streamed_choice = MagicMock()
        streamed_choice.message.content = "streamed description"
        streamed.choices = [streamed_choice]

        with (
            patch(
                "tools.vision_tools._image_to_base64_data_url",
                return_value="data:image/jpeg;base64,abc",
            ),
            patch(
                "tools.vision_tools._streamed_vision_completion",
                new_callable=AsyncMock,
                return_value=streamed,
            ),
            patch(
                "tools.vision_tools.async_call_llm",
                new_callable=AsyncMock,
            ) as router,
        ):
            result = await vt.vision_analyze_tool(
                self._data_url(), "describe", "test/model"
            )
        assert "streamed description" in result
        router.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_streaming_miss_falls_back_to_router(self):
        fallback = MagicMock()
        fallback_choice = MagicMock()
        fallback_choice.message.content = "router description"
        fallback.choices = [fallback_choice]

        with (
            patch(
                "tools.vision_tools._image_to_base64_data_url",
                return_value="data:image/jpeg;base64,abc",
            ),
            patch(
                "tools.vision_tools._streamed_vision_completion",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "tools.vision_tools.async_call_llm",
                new_callable=AsyncMock,
                return_value=fallback,
            ) as router,
        ):
            result = await vt.vision_analyze_tool(
                self._data_url(), "describe", "test/model"
            )
        assert "router description" in result
        router.assert_awaited_once()
