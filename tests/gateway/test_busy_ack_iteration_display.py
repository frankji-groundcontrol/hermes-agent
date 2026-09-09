"""Busy-status iteration text must not render the sys.maxsize sentinel.

Root cause 2026-09-08: agent_init defaults ``max_iterations = sys.maxsize``
and the gateway's effective budget (iteration_budget mechanism) never reaches
that attribute, so unguarded render sites showed
``iteration N/9223372036854775807``.
"""
import sys

from agent.session_activity import format_iteration_progress


def test_real_cap_renders_denominator():
    assert format_iteration_progress(5, 500) == "iteration 5/500"


def test_sentinel_renders_bare_iteration():
    assert format_iteration_progress(5, sys.maxsize) == "iteration 5"


def test_missing_cap_renders_bare_iteration():
    assert format_iteration_progress(5, 0) == "iteration 5"
    assert format_iteration_progress(5, None) == "iteration 5"


def test_non_numeric_cap_renders_bare_iteration():
    assert format_iteration_progress(5, "unlimited") == "iteration 5"
