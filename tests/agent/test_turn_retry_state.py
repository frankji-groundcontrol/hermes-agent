"""Unit tests for TurnRetryState (god-file Phase 1b).

The dataclass holds the inner-retry-loop's one-shot recovery guards + restart
signals. These tests cover fresh-state isolation and independent guards — the behavioral
guarantee for the loop itself is the existing recovery-branch tests in
tests/run_agent/ which now exercise these fields via `_retry.<flag>`.
"""

from __future__ import annotations

from agent.turn_retry_state import TurnRetryState


def test_developer_retry_state_is_fresh_for_each_api_call():
    used = TurnRetryState()
    used.developer_role_retry_messages = [{"role": "system", "content": "prompt"}]
    used.developer_role_retry_attempted = True
    used.api_output_started = True

    fresh = TurnRetryState()
    assert fresh.developer_role_retry_messages is None
    assert fresh.developer_role_retry_attempted is False
    assert fresh.api_output_started is False


def test_guards_are_independently_mutable():
    s = TurnRetryState()
    s.codex_auth_retry_attempted = True
    s.restart_with_compressed_messages = True
    assert s.codex_auth_retry_attempted is True
    assert s.restart_with_compressed_messages is True
    # untouched guards stay False
    assert s.has_retried_429 is False
    assert s.anthropic_auth_retry_attempted is False


def test_copilot_provider_check_accepts_alias_spellings():
    """`/model` and profile configs can leave `github-copilot` / `github` as
    the provider spelling; the recovery gates must not silently skip them."""
    from agent.conversation_loop import _is_copilot_provider
    from run_agent import AIAgent

    class _Agent:
        # Reuse the real single-owner check unbound; only provider/_base_url
        # state is faked.
        _is_copilot_provider = AIAgent._is_copilot_provider
        _is_copilot_url = AIAgent._is_copilot_url

        def __init__(self, provider, base_url=""):
            self.provider = provider
            self._base_url_lower = base_url.lower()

    assert _is_copilot_provider(_Agent("copilot"))
    assert _is_copilot_provider(_Agent("github-copilot"))
    assert _is_copilot_provider(_Agent("GitHub-Copilot"))
    assert _is_copilot_provider(_Agent("github"))
    # URL fallback: unnormalized provider but a Copilot base URL.
    assert _is_copilot_provider(_Agent("custom", "https://api.githubcopilot.com"))
    assert not _is_copilot_provider(_Agent("openrouter", "https://openrouter.ai/api/v1"))

    class _NoMethod:
        provider = "github-copilot"

    # Fallback path when the agent object lacks the method entirely.
    assert _is_copilot_provider(_NoMethod())
