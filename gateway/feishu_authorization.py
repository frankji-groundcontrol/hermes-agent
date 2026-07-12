"""Room-scoped Feishu authorization helper.

Shared by the Feishu adapter (``plugins.platforms.feishu.adapter``) and the
gateway runner (``gateway.authz_mixin``) so both admission layers honor the
same strict ``FEISHU_GROUP_ALLOWED_CHATS`` configuration.

A listed room grants room-scoped access to human senders. It does not override
``FEISHU_ALLOW_BOTS=none``, ``FEISHU_GROUP_POLICY=disabled``, missing room
context, DM restrictions, or mention requirements — those remain the
responsibility of each admission layer, which calls
:func:`is_feishu_group_chat_allowed` only inside its own absolute-gate guards.

Identifier shape: Feishu chat IDs are ``oc_``-prefixed (``oc_xxx``). User IDs
(``ou_``, ``u_``, ``on_``), message IDs (``om_``), and any non-conforming
value are rejected so a malformed list never silently admits a room.
"""

from __future__ import annotations

import os
import re

FEISHU_GROUP_ALLOWED_CHATS_ENV = "FEISHU_GROUP_ALLOWED_CHATS"
# ``\Z`` anchors to the whole token (equivalent to fullmatch over the stripped
# part). At least one character after the ``oc_`` prefix is required.
_FEISHU_CHAT_ID_RE = re.compile(r"oc_[A-Za-z0-9_-]+\Z")


def parse_feishu_group_allowed_chats(raw: str | None = None) -> frozenset[str]:
    """Parse ``FEISHU_GROUP_ALLOWED_CHATS`` into a set of valid chat IDs.

    Returns an empty set when the value is missing, blank, or contains any
    malformed entry. A single bad entry rejects the whole list rather than
    silently admitting a partially-parsed room.
    """
    if raw is None:
        raw = os.getenv(FEISHU_GROUP_ALLOWED_CHATS_ENV, "")
    if not raw.strip():
        return frozenset()

    parts = [part.strip() for part in raw.split(",")]
    if any(not part or not _FEISHU_CHAT_ID_RE.fullmatch(part) for part in parts):
        return frozenset()
    return frozenset(parts)


def is_feishu_group_chat_allowed(chat_id: object) -> bool:
    """Whether *chat_id* is a listed Feishu group/forum room."""
    normalized = str(chat_id or "").strip()
    return bool(normalized) and normalized in parse_feishu_group_allowed_chats()
