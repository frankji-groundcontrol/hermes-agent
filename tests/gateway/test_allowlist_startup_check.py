"""Tests for the startup allowlist warning helper in gateway/run.py.

Bound to the production helper ``gateway.run._warn_if_no_user_allowlist`` (no
copied logic). A valid, nonempty ``FEISHU_GROUP_ALLOWED_CHATS`` room set counts
as configured; missing/blank/malformed values do not.
"""

import pytest


def _clear_env(monkeypatch):
    """Blank the entire allowlist/allow-all env surface for one test."""
    for var in (
        "TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS",
        "WHATSAPP_ALLOWED_USERS", "WHATSAPP_CLOUD_ALLOWED_USERS",
        "SLACK_ALLOWED_USERS",
        "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "EMAIL_ALLOWED_USERS",
        "SMS_ALLOWED_USERS", "MATTERMOST_ALLOWED_USERS",
        "MATRIX_ALLOWED_USERS", "DINGTALK_ALLOWED_USERS",
        "FEISHU_ALLOWED_USERS",
        "FEISHU_GROUP_ALLOWED_CHATS",
        "WECOM_ALLOWED_USERS",
        "WECOM_CALLBACK_ALLOWED_USERS",
        "WEIXIN_ALLOWED_USERS",
        "BLUEBUBBLES_ALLOWED_USERS",
        "QQ_ALLOWED_USERS",
        "YUANBAO_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "TELEGRAM_ALLOW_ALL_USERS", "DISCORD_ALLOW_ALL_USERS",
        "WHATSAPP_ALLOW_ALL_USERS", "WHATSAPP_CLOUD_ALLOW_ALL_USERS",
        "SLACK_ALLOW_ALL_USERS",
        "SIGNAL_ALLOW_ALL_USERS", "EMAIL_ALLOW_ALL_USERS",
        "SMS_ALLOW_ALL_USERS", "MATTERMOST_ALLOW_ALL_USERS",
        "MATRIX_ALLOW_ALL_USERS", "DINGTALK_ALLOW_ALL_USERS",
        "FEISHU_ALLOW_ALL_USERS",
        "WECOM_ALLOW_ALL_USERS",
        "WECOM_CALLBACK_ALLOW_ALL_USERS",
        "WEIXIN_ALLOW_ALL_USERS",
        "BLUEBUBBLES_ALLOW_ALL_USERS",
        "QQ_ALLOW_ALL_USERS",
        "YUANBAO_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(var, raising=False)


class TestAllowlistStartupCheck:
    def test_no_config_emits_warning(self, monkeypatch):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        assert _warn_if_no_user_allowlist() is True

    def test_signal_group_allowed_users_suppresses_warning(self, monkeypatch):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("SIGNAL_GROUP_ALLOWED_USERS", "user1")
        assert _warn_if_no_user_allowlist() is False

    def test_telegram_allow_all_users_suppresses_warning(self, monkeypatch):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", "true")
        assert _warn_if_no_user_allowlist() is False

    def test_gateway_allow_all_users_suppresses_warning(self, monkeypatch):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "yes")
        assert _warn_if_no_user_allowlist() is False

    def test_feishu_allowed_users_suppresses_warning(self, monkeypatch):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("FEISHU_ALLOWED_USERS", "ou_human")
        assert _warn_if_no_user_allowlist() is False

    @pytest.mark.parametrize("raw", ["oc_listed", "oc_listed,oc_other", "  oc_listed  "])
    def test_valid_room_allowlist_suppresses_warning(self, monkeypatch, raw):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", raw)
        assert _warn_if_no_user_allowlist() is False

    @pytest.mark.parametrize(
        "raw",
        [
            "",                 # empty
            "   ",              # blank
            "ou_not_a_chat",    # non-chat id
            "oc_listed,,oc_other",  # empty entry
            "oc_listed\noc_other",  # newline-separated
            "garbage",          # malformed
        ],
    )
    def test_missing_blank_or_malformed_room_config_warns(self, monkeypatch, raw):
        from gateway.run import _warn_if_no_user_allowlist

        _clear_env(monkeypatch)
        monkeypatch.setenv("FEISHU_GROUP_ALLOWED_CHATS", raw)
        assert _warn_if_no_user_allowlist() is True
