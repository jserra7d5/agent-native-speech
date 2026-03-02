"""Multi-session registry for the voice agent MCP server.

Wraps CallManager to track AgentSessions — one per connected MCP client.
Each session maps an MCP session ID to an AgentSession which holds metadata
(name, client type, directory, voice assignment, status) and optionally a
CallSession for active voice operations.

Downstream phases (voice pool, switchboard, spawn) will extend this module.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from server.call_manager import CallManager, CallSession
from server.config import Config
from server.discord_bot import BotRunner
from server.message_manager import MessageManager, MessageSession
from server.speech_mode import SpeechModeManager
from server.stt_pipeline import STTPipeline
from server.switchboard import Switchboard
from server.tts_backend import TTSBackend
from server.voice_pool import VoicePool

log = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """Represents a connected MCP client session.

    Attributes:
        session_id: Unique identifier (UUID string) for this agent session.
        session_name: Human-readable display name (directory basename, auto-suffixed).
        client_type: Which CLI is connected ("claude", "codex", "other").
        directory: Absolute path to the agent's working directory.
        voice_name: Assigned TTS voice (None if only session / not yet assigned).
        spawn_mode: How the CLI was launched ("interactive" or "headless").
        process_pid: OS PID of the spawned process (None if connected externally).
        terminal_pid: OS PID of the terminal emulator (None if headless).
        mcp_session_id: Streamable HTTP session ID for this client.
        status: Current lifecycle state.
        started_at: Unix timestamp when the session was created.
        last_activity: Unix timestamp of most recent tool call.
        owning_user_id: Discord user ID who spawned this session.
        call_session: Active voice CallSession (None if not in a call).
    """

    session_id: str
    session_name: str = ""
    client_type: str = "other"
    directory: str = ""
    voice_name: str | None = None
    spawn_mode: str = "interactive"
    process_pid: int | None = None
    terminal_pid: int | None = None
    mcp_session_id: str | None = None
    status: str = "connected"
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    owning_user_id: str = ""
    call_session: CallSession | None = None
    mode: str = "voice"  # "voice" or "message"
    text_channel_id: int | None = None
    message_session: MessageSession | None = None


class SessionManager:
    """Multi-session registry wrapping CallManager.

    Tracks AgentSessions by session_id, maps MCP session IDs to agent
    sessions, and delegates voice operations to the underlying CallManager.
    """

    def __init__(
        self,
        bot_runner: BotRunner,
        stt_pipeline: STTPipeline,
        tts_engine: TTSBackend,
        speech_mode_manager: SpeechModeManager | None = None,
        config: Config | None = None,
        message_manager: MessageManager | None = None,
    ) -> None:
        self._call_manager = CallManager(
            bot_runner, stt_pipeline, tts_engine, speech_mode_manager,
            config=config,
        )
        self._message_manager = message_manager
        self._runner = bot_runner
        self._sessions: dict[str, AgentSession] = {}
        # MCP session ID -> agent session ID mapping
        self._mcp_to_session: dict[str, str] = {}
        # Track used names for collision avoidance
        self._name_counter: Counter[str] = Counter()
        # Voice pool for per-session TTS voice assignment
        self._voice_pool = VoicePool.from_config(config) if config else VoicePool()
        # Switchboard for multi-session message routing
        max_depth = config.max_queue_depth if config else 20
        self._switchboard = Switchboard(max_queue_depth=max_depth)

    @property
    def call_manager(self) -> CallManager:
        """Access the underlying CallManager for voice operations."""
        return self._call_manager

    @property
    def message_manager(self) -> MessageManager | None:
        """Access the underlying MessageManager for text message operations."""
        return self._message_manager

    def set_message_manager(self, manager: MessageManager) -> None:
        """Set the MessageManager after construction (for late wiring)."""
        self._message_manager = manager

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def register_session(
        self,
        mcp_session_id: str | None = None,
        session_name: str | None = None,
        client_type: str = "other",
        directory: str = "",
        spawn_mode: str = "interactive",
        process_pid: int | None = None,
        terminal_pid: int | None = None,
        owning_user_id: str = "",
        requested_voice: str | None = None,
        mode: str = "voice",
        text_channel_id: int | None = None,
    ) -> AgentSession:
        """Register a new agent session.

        Args:
            mcp_session_id: Streamable HTTP session ID (None for stdio).
            session_name: Display name. Defaults to directory basename.
            client_type: CLI type ("claude", "codex", "other").
            directory: Working directory path.
            spawn_mode: "interactive" or "headless".
            process_pid: OS PID of the spawned process.
            terminal_pid: OS PID of the terminal emulator.
            owning_user_id: Discord user ID of the spawner.
            requested_voice: Optional explicit TTS voice preference.
            mode: Session mode ("voice" or "message").
            text_channel_id: Discord text channel ID for message mode.

        Returns:
            The newly created AgentSession.
        """
        session_id = str(uuid.uuid4())

        # Auto-generate name from directory basename
        if not session_name:
            if directory:
                session_name = os.path.basename(directory)
            else:
                session_name = "session"

        # Auto-suffix on collision
        session_name = self._unique_name(session_name)

        session = AgentSession(
            session_id=session_id,
            session_name=session_name,
            client_type=client_type,
            directory=directory,
            spawn_mode=spawn_mode,
            process_pid=process_pid,
            terminal_pid=terminal_pid,
            mcp_session_id=mcp_session_id,
            owning_user_id=owning_user_id,
            mode=mode,
            text_channel_id=text_channel_id,
        )
        self._sessions[session_id] = session
        if mcp_session_id:
            self._mcp_to_session[mcp_session_id] = session_id

        # Assign a TTS voice from the pool (even for message mode, for consistency)
        assigned_voice = self._voice_pool.assign_voice(session_id, requested_voice)
        session.voice_name = assigned_voice

        # Register with the switchboard
        self._switchboard.register_session(session_id, session_name)

        log.info(
            "Registered session %s (%s) mcp=%s voice=%s mode=%s",
            session_id,
            session_name,
            mcp_session_id,
            assigned_voice,
            mode,
        )
        return session

    def unregister_session(self, session_id: str) -> None:
        """Remove a session from the registry.

        Cleans up any active call session and releases resources.
        """
        # Release voice and drain switchboard queue before removing
        self._voice_pool.release_voice(session_id)
        self._switchboard.unregister_session(session_id)

        session = self._sessions.pop(session_id, None)
        if session is None:
            log.warning("Attempted to unregister unknown session %s", session_id)
            return

        # Clean up MCP mapping
        if session.mcp_session_id and session.mcp_session_id in self._mcp_to_session:
            del self._mcp_to_session[session.mcp_session_id]

        # Release name from counter
        base_name = session.session_name
        # Strip any auto-suffix for counter tracking
        self._name_counter[base_name] -= 1
        if self._name_counter[base_name] <= 0:
            del self._name_counter[base_name]

        # Clean up any active call session in CallManager
        if session.call_session:
            call_id = session.call_session.call_id
            if call_id in self._call_manager._sessions:
                del self._call_manager._sessions[call_id]

        # Clean up any active message session in MessageManager
        if session.message_session and self._message_manager:
            call_id = session.message_session.call_id
            if call_id in self._message_manager._sessions:
                del self._message_manager._sessions[call_id]
            # Cancel any pending reply future
            pending = self._message_manager._pending_replies.pop(call_id, None)
            if pending is not None:
                future, loop = pending
                if not future.done():
                    loop.call_soon_threadsafe(future.cancel)

        session.status = "disconnected"
        log.info("Unregistered session %s (%s)", session_id, session.session_name)

    def get_session(self, session_id: str) -> AgentSession:
        """Get a session by ID. Raises KeyError if not found."""
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"No session with id '{session_id}'") from None

    def get_session_by_mcp_id(self, mcp_session_id: str) -> AgentSession | None:
        """Look up a session by its MCP transport session ID."""
        session_id = self._mcp_to_session.get(mcp_session_id)
        if session_id is None:
            return None
        return self._sessions.get(session_id)

    def list_active_sessions(self) -> list[dict[str, Any]]:
        """Return all active sessions as serializable dicts."""
        result = []
        for session in self._sessions.values():
            if session.status == "disconnected":
                continue
            entry: dict[str, Any] = {
                "session_id": session.session_id,
                "session_name": session.session_name,
                "client_type": session.client_type,
                "directory": session.directory,
                "voice": session.voice_name or "",
                "status": session.status,
                "spawn_mode": session.spawn_mode,
                "mode": session.mode,
                "started_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(session.started_at)
                ),
                "has_queued_messages": self._switchboard.has_pending_for_session(
                    session.session_id
                ),
                "queued_message_count": self._switchboard.pending_count_for_session(
                    session.session_id
                ),
            }
            result.append(entry)
        return result

    def resolve_voice(self, session_id: str) -> str:
        """Return the effective TTS voice for a session.

        Single-session mode uses the default voice; multi-session mode
        uses the pool-assigned voice.
        """
        return self._voice_pool.resolve_voice(session_id)

    @property
    def voice_pool(self) -> VoicePool:
        """Access the underlying VoicePool."""
        return self._voice_pool

    @property
    def switchboard(self) -> Switchboard:
        """Access the underlying Switchboard."""
        return self._switchboard

    # ------------------------------------------------------------------
    # Voice operations (delegated to CallManager)
    # ------------------------------------------------------------------

    def _get_mode_for_call_id(self, call_id: str) -> str:
        """Determine the mode (voice/message) for a given call_id."""
        # Check message manager first
        if self._message_manager and call_id in self._message_manager._sessions:
            return "message"
        # Check call manager
        if call_id in self._call_manager._sessions:
            return "voice"
        # Fall back to checking agent sessions
        agent_session = self._find_session_by_call_id(call_id)
        if agent_session:
            return agent_session.mode
        return "voice"

    async def initiate_call(
        self,
        channel_id: int,
        message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Join a voice channel or send a text message, depending on mode.

        If session_id is provided, links the call to that agent session.
        Routes to MessageManager for message-mode sessions.
        """
        # Check if this session is in message mode
        agent_session = self._sessions.get(session_id) if session_id else None
        if agent_session and agent_session.mode == "message":
            if self._message_manager is None:
                raise RuntimeError("MessageManager not available for message mode")
            result = await self._message_manager.initiate_call(
                channel_id=channel_id, message=message,
                user_id=agent_session.owning_user_id,
            )
            call_id = result["call_id"]
            # Link the message session to the agent session
            msg_session = self._message_manager._sessions.get(call_id)
            if msg_session:
                agent_session.message_session = msg_session
                agent_session.status = "working"
                agent_session.last_activity = time.time()
            result["session_id"] = session_id
            return result

        # Voice mode (default)
        voice = self.resolve_voice(session_id) if session_id else None
        result = await self._call_manager.initiate_call(
            channel_id=channel_id, message=message, voice=voice
        )
        call_id = result["call_id"]

        # Link the call to the agent session if provided
        if session_id and session_id in self._sessions:
            agent_session = self._sessions[session_id]
            call_session = self._call_manager._sessions.get(call_id)
            if call_session:
                agent_session.call_session = call_session
                agent_session.status = "working"
                agent_session.last_activity = time.time()
            result["session_id"] = session_id

        return result

    async def continue_call(
        self, call_id: str, message: str
    ) -> dict[str, Any]:
        """Speak/send and listen/wait on an active call."""
        mode = self._get_mode_for_call_id(call_id)
        if mode == "message":
            if self._message_manager is None:
                raise RuntimeError("MessageManager not available for message mode")
            result = await self._message_manager.continue_call(
                call_id=call_id, message=message
            )
            self._touch_session_by_call_id(call_id)
            return result

        agent_session = self._find_session_by_call_id(call_id)
        voice = self.resolve_voice(agent_session.session_id) if agent_session else None
        result = await self._call_manager.continue_call(
            call_id=call_id, message=message, voice=voice
        )
        self._touch_session_by_call_id(call_id)
        return result

    async def speak_to_user(
        self, call_id: str, message: str
    ) -> dict[str, Any]:
        """Speak/send without listening/waiting."""
        mode = self._get_mode_for_call_id(call_id)
        if mode == "message":
            if self._message_manager is None:
                raise RuntimeError("MessageManager not available for message mode")
            result = await self._message_manager.speak_to_user(
                call_id=call_id, message=message
            )
            self._touch_session_by_call_id(call_id)
            return result

        agent_session = self._find_session_by_call_id(call_id)
        voice = self.resolve_voice(agent_session.session_id) if agent_session else None
        result = await self._call_manager.speak_to_user(
            call_id=call_id, message=message, voice=voice
        )
        self._touch_session_by_call_id(call_id)
        return result

    async def end_call(
        self, call_id: str, message: str
    ) -> dict[str, Any]:
        """End a call/session and clean up the agent session."""
        mode = self._get_mode_for_call_id(call_id)
        if mode == "message":
            if self._message_manager is None:
                raise RuntimeError("MessageManager not available for message mode")
            agent_session = self._find_session_by_call_id(call_id)
            result = await self._message_manager.end_call(
                call_id=call_id, message=message
            )
            if agent_session:
                self.unregister_session(agent_session.session_id)
            return result

        # Voice mode
        # Find the agent session before ending (CallManager removes it)
        agent_session = self._find_session_by_call_id(call_id)
        voice = self.resolve_voice(agent_session.session_id) if agent_session else None
        result = await self._call_manager.end_call(
            call_id=call_id, message=message, voice=voice
        )
        # Unregister the agent session — once the call ends, the voice
        # session is over.  The spawned CLI process may still be running
        # but it's no longer a voice session.
        if agent_session:
            self.unregister_session(agent_session.session_id)
        return result

    # ------------------------------------------------------------------
    # Correction pass-through
    # ------------------------------------------------------------------

    def add_correction(
        self, wrong: str, right: str, user_id: str = "default"
    ) -> dict[str, Any]:
        return self._call_manager.add_correction(wrong=wrong, right=right, user_id=user_id)

    def list_corrections(self, user_id: str = "default") -> dict[str, Any]:
        return self._call_manager.list_corrections(user_id=user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _unique_name(self, base: str) -> str:
        """Generate a unique session name, auto-suffixing on collision."""
        self._name_counter[base] += 1
        count = self._name_counter[base]
        if count == 1:
            return base
        return f"{base}-{count}"

    def _find_session_by_call_id(self, call_id: str) -> AgentSession | None:
        """Find the agent session linked to a call_id (voice or message)."""
        for session in self._sessions.values():
            if session.call_session and session.call_session.call_id == call_id:
                return session
            if session.message_session and session.message_session.call_id == call_id:
                return session
        return None

    def _touch_session_by_call_id(self, call_id: str) -> None:
        """Update last_activity on the session owning this call."""
        session = self._find_session_by_call_id(call_id)
        if session:
            session.last_activity = time.time()
            session.status = "working"
