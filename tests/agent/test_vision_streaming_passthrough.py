"""Data-URL passthrough in the anthropic image fallback.

The fallback used to materialize data URLs to host ``/tmp/anthropic_image_*``
before calling ``vision_analyze_tool`` — under a docker terminal backend that
path is outside the media caches and unreadable (SourceNotFound), so the
auto-describe always failed even when the vision API itself was healthy. The
data URL now passes through unchanged.
"""

import json
import tempfile

import tools.vision_tools as vt
from run_agent import AIAgent


def _agent():
    agent = AIAgent.__new__(AIAgent)
    agent._anthropic_image_fallback_cache = {}
    return agent


def test_data_url_passed_through_untouched(monkeypatch, tmp_path):
    calls = {}

    async def fake_tool(image_url=None, user_prompt=None, **kwargs):
        calls["image_url"] = image_url
        return json.dumps({"success": True, "analysis": "a red box"})

    monkeypatch.setattr(vt, "vision_analyze_tool", fake_tool)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    data_url = "data:image/png;base64,aGVsbG8="
    note = _agent()._describe_image_for_anthropic_fallback(data_url, "user")

    assert calls["image_url"] == data_url
    assert "a red box" in note
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("anthropic_image_")]
    assert leftovers == [], f"materialized temp files despite passthrough: {leftovers}"
