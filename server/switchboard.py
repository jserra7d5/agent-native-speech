"""Voice switchboard for multi-session message routing.

Manages per-session message queues and coordinates message delivery
between agents and the user. When multiple sessions are active, messages
are prefixed with the session name so the user can tell them apart.

The switchboard tracks:
  - Per-session message queues (agent-to-user and user-to-agent)
  - Last-speaker session for default routing
  - Pending announcements for System Voice TTS
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A single pending message in a session's queue.

    Attributes:
        message_id: Unique message identifier (UUID).
        direction: Who sent the message ("agent_to_user" or "user_to_agent").
        sender_session: Session ID of the sender (None if from user).
        content: Message text content.
        timestamp: Unix timestamp when queued.
        delivered: Whether the message has been read out or delivered.
    """

    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    direction: str = "agent_to_user"  # "agent_to_user" | "user_to_agent"
    sender_session: str | None = None
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    delivered: bool = False


class MessageQueue:
    """Per-session ordered queue of pending messages.

    Enforces a maximum depth to prevent unbounded memory growth.
    """

    def __init__(self, session_id: str, max_depth: int = 20) -> None:
        self.session_id = session_id
        self.max_depth = max_depth
        self._messages: list[QueuedMessage] = []

    def enqueue(self, message: QueuedMessage) -> bool:
        """Add a message to the queue. Returns False if queue is full."""
        if len(self._messages) >= self.max_depth:
            log.warning(
                "Queue full for session %s (max_depth=%d), dropping message",
                self.session_id,
                self.max_depth,
            )
            return False
        self._messages.append(message)
        return True

    def get_pending(self, direction: str | None = None) -> list[QueuedMessage]:
        """Get all undelivered messages, optionally filtered by direction."""
        return [
            m for m in self._messages
            if not m.delivered and (direction is None or m.direction == direction)
        ]

    def deliver_next(self, direction: str | None = None) -> QueuedMessage | None:
        """Mark and return the next undelivered message."""
        for msg in self._messages:
            if not msg.delivered and (direction is None or msg.direction == direction):
                msg.delivered = True
                return msg
        return None

    def mark_all_delivered(self, direction: str | None = None) -> int:
        """Mark all pending messages as delivered. Returns count marked."""
        count = 0
        for msg in self._messages:
            if not msg.delivered and (direction is None or msg.direction == direction):
                msg.delivered = True
                count += 1
        return count

    def drain(self) -> list[QueuedMessage]:
        """Remove and return all messages (delivered and undelivered)."""
        messages = self._messages[:]
        self._messages.clear()
        return messages

    @property
    def pending_count(self) -> int:
        """Number of undelivered messages."""
        return sum(1 for m in self._messages if not m.delivered)

    @property
    def has_pending(self) -> bool:
        return self.pending_count > 0

    def __len__(self) -> int:
        return len(self._messages)


class Switchboard:
    """Multi-session message switchboard.

    Coordinates message delivery between agents and the user:
      - Agents enqueue messages for the user (agent_to_user)
      - The user can send cold-call messages to agents (user_to_agent)
      - System Voice announces pending messages before listening
      - Messages are prefixed with session name when multiple sessions active
    """

    def __init__(self, max_queue_depth: int = 20) -> None:
        self._max_queue_depth = max_queue_depth
        self._queues: dict[str, MessageQueue] = {}
        # Track the last session that spoke to the user
        self._last_speaker_session: str | None = None
        # Session name lookup (session_id -> name) for message prefixing
        self._session_names: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def register_session(self, session_id: str, session_name: str = "") -> None:
        """Create a message queue for a new session."""
        if session_id not in self._queues:
            self._queues[session_id] = MessageQueue(
                session_id, max_depth=self._max_queue_depth
            )
            self._session_names[session_id] = session_name
            log.info("Switchboard: registered session %s (%s)", session_id, session_name)

    def unregister_session(self, session_id: str) -> list[QueuedMessage]:
        """Remove a session's queue. Returns any remaining messages."""
        self._session_names.pop(session_id, None)
        queue = self._queues.pop(session_id, None)
        if self._last_speaker_session == session_id:
            self._last_speaker_session = None
        if queue:
            remaining = queue.drain()
            log.info(
                "Switchboard: unregistered session %s, drained %d messages",
                session_id,
                len(remaining),
            )
            return remaining
        return []

    def update_session_name(self, session_id: str, name: str) -> None:
        """Update the display name for a session."""
        self._session_names[session_id] = name

    # ------------------------------------------------------------------
    # Message enqueueing
    # ------------------------------------------------------------------

    def enqueue_agent_message(
        self, session_id: str, content: str
    ) -> QueuedMessage | None:
        """Enqueue a message from an agent to the user.

        Called when an agent wants to speak to the user but another
        session currently has the voice channel.

        Args:
            session_id: The sending agent's session ID.
            content: Message text to deliver.

        Returns:
            The queued message, or None if the queue is full.
        """
        queue = self._get_or_create_queue(session_id)
        msg = QueuedMessage(
            direction="agent_to_user",
            sender_session=session_id,
            content=content,
        )
        if queue.enqueue(msg):
            log.info(
                "Switchboard: enqueued agent->user message for session %s",
                session_id,
            )
            return msg
        return None

    def enqueue_user_message(
        self, session_id: str, content: str
    ) -> QueuedMessage | None:
        """Enqueue a cold-call message from the user to an agent.

        Called when the user wants to send a message to an agent that
        is currently busy working (not listening).

        Args:
            session_id: The target agent's session ID.
            content: Message text to deliver.

        Returns:
            The queued message, or None if the queue is full.
        """
        queue = self._get_or_create_queue(session_id)
        msg = QueuedMessage(
            direction="user_to_agent",
            sender_session=None,
            content=content,
        )
        if queue.enqueue(msg):
            log.info(
                "Switchboard: enqueued user->agent cold call for session %s",
                session_id,
            )
            return msg
        return None

    # ------------------------------------------------------------------
    # Message delivery
    # ------------------------------------------------------------------

    def get_pending_announcements(
        self, session_count: int = 1
    ) -> list[dict[str, Any]]:
        """Get all pending agent-to-user messages for System Voice announcement.

        Returns formatted announcements, prefixed with session name when
        multiple sessions are active.

        Args:
            session_count: Number of active sessions (for name prefixing).

        Returns:
            List of dicts with keys: session_id, session_name, content,
            message_id, timestamp.
        """
        announcements = []
        for session_id, queue in self._queues.items():
            pending = queue.get_pending(direction="agent_to_user")
            session_name = self._session_names.get(session_id, session_id[:8])
            for msg in pending:
                content = msg.content
                if session_count > 1:
                    content = f"{session_name}: {content}"
                announcements.append({
                    "session_id": session_id,
                    "session_name": session_name,
                    "content": content,
                    "message_id": msg.message_id,
                    "timestamp": msg.timestamp,
                })
        return announcements

    def deliver_next_message(
        self, session_id: str
    ) -> QueuedMessage | None:
        """Deliver the next pending user-to-agent message for a session.

        Marks the message as delivered and returns it.
        """
        queue = self._queues.get(session_id)
        if queue is None:
            return None
        return queue.deliver_next(direction="user_to_agent")

    def get_pending_user_messages(
        self, session_id: str
    ) -> list[QueuedMessage]:
        """Get all undelivered user-to-agent messages for a session.

        Used by the check_messages MCP tool.
        """
        queue = self._queues.get(session_id)
        if queue is None:
            return []
        return queue.get_pending(direction="user_to_agent")

    def mark_messages_delivered(
        self, session_id: str, direction: str | None = None
    ) -> int:
        """Mark all pending messages for a session as delivered."""
        queue = self._queues.get(session_id)
        if queue is None:
            return 0
        return queue.mark_all_delivered(direction=direction)

    # ------------------------------------------------------------------
    # Routing state
    # ------------------------------------------------------------------

    @property
    def last_speaker_session(self) -> str | None:
        """The session ID that last spoke to the user."""
        return self._last_speaker_session

    def set_last_speaker(self, session_id: str) -> None:
        """Update the last-speaker session for default routing."""
        self._last_speaker_session = session_id

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_pending_for_session(self, session_id: str) -> bool:
        """Check if a session has any undelivered messages."""
        queue = self._queues.get(session_id)
        return queue.has_pending if queue else False

    def pending_count_for_session(self, session_id: str) -> int:
        """Get the number of undelivered messages for a session."""
        queue = self._queues.get(session_id)
        return queue.pending_count if queue else 0

    def get_queue_info(self) -> dict[str, dict[str, int]]:
        """Get summary of all queues. Returns {session_id: {pending, total}}."""
        return {
            sid: {"pending": q.pending_count, "total": len(q)}
            for sid, q in self._queues.items()
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_queue(self, session_id: str) -> MessageQueue:
        """Get the queue for a session, creating it if needed."""
        if session_id not in self._queues:
            self._queues[session_id] = MessageQueue(
                session_id, max_depth=self._max_queue_depth
            )
        return self._queues[session_id]
