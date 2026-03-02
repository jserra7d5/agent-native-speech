"""check_messages MCP tool implementation.

Queries the Switchboard for pending user-to-agent messages for the
calling session, returns them as structured JSON per the MCP contract,
and marks them as delivered.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from server.switchboard import Switchboard

log = logging.getLogger(__name__)


def check_messages(
    switchboard: Switchboard,
    session_id: str,
) -> dict[str, Any]:
    """Retrieve pending voice messages for a session.

    Fetches all undelivered user-to-agent messages from the switchboard,
    marks them as delivered, and returns them in the contract format.

    Args:
        switchboard: The active Switchboard instance.
        session_id: The calling agent's session ID.

    Returns:
        Dict with "messages" key containing a list of message dicts,
        each with message_id, from, content, and timestamp fields.
    """
    pending = switchboard.get_pending_user_messages(session_id)

    messages = []
    for msg in pending:
        msg.delivered = True
        messages.append({
            "message_id": msg.message_id,
            "from": "user",
            "content": msg.content,
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(msg.timestamp)
            ),
        })

    if messages:
        log.info(
            "Delivered %d pending message(s) to session %s",
            len(messages),
            session_id,
        )

    return {"messages": messages}
