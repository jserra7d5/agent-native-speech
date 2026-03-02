"""Session and call state management for the Discord voice bot MCP server.

Manages the lifecycle of voice call sessions: joining channels, tracking
conversation history, and coordinating TTS/STT pipelines.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import discord

from server.audio_source import StreamingAudioSource, TTSAudioSource
from server.tts_backend import TTSBackend, preprocess as _preprocess
from server.discord_bot import BotRunner
from server.speech_mode import SpeechModeManager
from server.stt_pipeline import STTPipeline

log = logging.getLogger(__name__)


@dataclass
class CallSession:
    """Represents a single active voice call session.

    Attributes:
        call_id: Unique identifier (UUID string) for this session.
        channel_id: Discord voice channel ID the bot is connected to.
        guild_id: Discord guild (server) ID containing the channel.
        voice_client: The discord.py VoiceClient (or VoiceRecvClient) instance.
        text_channel: Optional text channel for transcript posting.
        started_at: Unix timestamp (float) when the call was initiated.
        conversation_history: Ordered list of message dicts with keys
            "role" ("user" | "assistant") and "content" (str).
    """

    call_id: str
    channel_id: int
    guild_id: int
    voice_client: discord.VoiceClient
    text_channel: discord.TextChannel | None
    started_at: float
    conversation_history: list[dict[str, str]] = field(default_factory=list)


class CallManager:
    """Bridges the MCP server (main asyncio thread) with the Discord bot thread.

    The MCP server is async but runs in the main thread. The Discord bot
    runs in a background thread with its own event loop. All Discord
    operations are dispatched via BotRunner.run_coroutine() (blocking) or
    BotRunner.run_coroutine_async() (non-blocking future).
    """

    def __init__(
        self,
        bot_runner: BotRunner,
        stt_pipeline: STTPipeline,
        tts_engine: TTSBackend,
        speech_mode_manager: SpeechModeManager | None = None,
    ) -> None:
        """Initialise the manager.

        Args:
            bot_runner: The running BotRunner that wraps the Discord bot.
            stt_pipeline: Shared STT pipeline (VAD + Whisper + LLM correction).
            tts_engine: Shared TTS engine (Qwen3-TTS).
            speech_mode_manager: Optional speech mode manager for stop-token support.
        """
        self._runner = bot_runner
        self._stt = stt_pipeline
        self._tts_engine = tts_engine
        self._speech_mode = speech_mode_manager
        # Keyed by call_id (str UUID)
        self._sessions: dict[str, CallSession] = {}

        # Register the leave-callback so the manager can clean up when a user
        # leaves without an explicit end_call.
        self._runner.bot._on_user_leave = self._on_user_leave_sync

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_user_leave_sync(self, channel_id: int) -> None:
        """Synchronous callback invoked from the Discord bot thread when all
        users have left a voice channel.  Finds any active session for the
        channel and removes it from the registry.
        """
        to_remove = [
            cid for cid, session in self._sessions.items()
            if session.channel_id == channel_id
        ]
        for call_id in to_remove:
            log.info(
                "Auto-cleaning session %s because all users left channel %d",
                call_id,
                channel_id,
            )
            del self._sessions[call_id]

    def _get_session(self, call_id: str) -> CallSession:
        """Return the session or raise KeyError with a helpful message."""
        try:
            return self._sessions[call_id]
        except KeyError:
            raise KeyError(f"No active call session with id '{call_id}'") from None

    async def _tts_speak(
        self,
        voice_client: discord.VoiceClient,
        message: str,
        voice: str | None = None,
    ) -> None:
        """Synthesise speech and play it over the voice channel.

        For single-sentence messages the old non-streaming path is used to
        avoid unnecessary overhead.  For multi-sentence messages a
        :class:`~server.audio_source.StreamingAudioSource` is used: a
        background thread synthesises chunks one at a time and feeds them to
        the source while Discord's audio thread is already playing back the
        first sentence, dramatically reducing time-to-first-audio.
        """
        if not voice_client.is_connected():
            log.warning("Voice client disconnected; cannot %s", "speak")
            return

        if not message.strip():
            return

        loop = asyncio.get_running_loop()

        short_msg = message[:80] + "..." if len(message) > 80 else message
        log.info("Synthesizing TTS for: %r", short_msg)

        # ------------------------------------------------------------------
        # Fast path: single sentence — synthesise fully then play.
        # Avoids StreamingAudioSource overhead for short utterances.
        # ------------------------------------------------------------------
        chunks = _preprocess(message)
        if len(chunks) <= 1:
            audio, sample_rate = await loop.run_in_executor(
                None, self._tts_engine.synthesize, message, voice
            )
            source = TTSAudioSource.from_audio(audio, sample_rate=sample_rate)
            try:
                voice_client.play(source)
                # Poll threading.Event from the async loop
                while not source.done.is_set():
                    await asyncio.sleep(0.05)
                log.debug("TTS playback complete (%.2f s)", source.duration_seconds)
            except discord.ClientException as exc:
                log.error("Failed to play TTS audio (ClientException): %s", exc)
            except Exception as exc:
                log.error("Unexpected error during TTS playback: %s", exc)
            return

        # ------------------------------------------------------------------
        # Streaming path: multiple sentences — overlap synthesis and playback.
        # A worker thread calls synthesize_streamed() and pushes segments into
        # StreamingAudioSource as they become available.  Discord starts
        # playing the first segment before the rest are synthesised.
        # ------------------------------------------------------------------
        log.debug(
            "Using streaming TTS path (%d sentence chunks)", len(chunks)
        )
        source = StreamingAudioSource()

        def _synth_worker() -> None:
            try:
                for audio, sr in self._tts_engine.synthesize_streamed(message, voice):
                    source.add_segment(audio, sr)
            except Exception:
                log.exception("Error in TTS synthesis worker")
            finally:
                source.finish()

        synth_future = loop.run_in_executor(None, _synth_worker)

        try:
            voice_client.play(source)
            # Wait for synthesis to finish and for all audio to be consumed.
            await synth_future
            while not source.done.is_set():
                await asyncio.sleep(0.05)
            log.debug("Streaming TTS playback complete")
        except discord.ClientException as exc:
            log.error("Failed to play streaming TTS audio (ClientException): %s", exc)
            source.finish()
            await synth_future
        except Exception as exc:
            log.error("Unexpected error during streaming TTS playback: %s", exc)
            source.finish()
            await synth_future

    async def _stt_listen(
        self,
        voice_client: discord.VoiceClient,
        session: CallSession,
    ) -> str:
        """Listen on the voice channel and return a corrected transcript.

        Delegates to the shared STTPipeline which handles:
          1. Attaching a UserAudioSink to the voice client
          2. Streaming audio through Silero VAD for speech boundary detection
          3. Transcribing with Faster-Whisper
          4. Applying LLM-based corrections via the user's correction dictionary
        """
        if not voice_client.is_connected():
            log.warning("Voice client disconnected; cannot %s", "listen")
            return ""

        # Resolve the target user from voice channel members
        user = self._resolve_voice_user(voice_client, session)
        if user is None:
            log.warning(
                "No human user found in voice channel %d; cannot listen",
                session.channel_id,
            )
            return ""

        user_id = str(user.id)
        return await self._stt.listen(
            voice_client=voice_client,
            user=user,
            user_id=user_id,
            speech_mode=self._speech_mode,
        )

    def _resolve_voice_user(
        self,
        voice_client: discord.VoiceClient,
        session: CallSession,
    ) -> discord.Member | None:
        """Find the first non-bot human user in the voice channel."""
        channel = voice_client.channel
        if channel is None:
            return None
        for member in channel.members:
            if not member.bot:
                return member
        return None

    def _post_to_text_channel(
        self,
        session: CallSession,
        role: str,
        content: str,
    ) -> None:
        """Post a conversation turn to the session's text channel.

        Dispatches the Discord send() call onto the bot's event loop via
        run_coroutine() so it is safe to call from any thread or coroutine
        running outside the bot's event loop.

        Args:
            session: The active call session (provides text_channel).
            role: Either "user" or "assistant"; controls the prefix label.
            content: The message text to post.
        """
        if session.text_channel is None:
            return

        if role == "user":
            formatted = f"🎤 **User**: {content}"
        else:
            formatted = f"🤖 **Agent**: {content}"

        try:
            self._runner.run_coroutine(session.text_channel.send(formatted))
        except Exception:
            log.exception(
                "Failed to post %s turn to text channel %s (call %s)",
                role,
                session.text_channel.id,
                session.call_id,
            )

    # ------------------------------------------------------------------
    # Public call API (called from MCP tool handlers via asyncio.run_coroutine_threadsafe)
    # ------------------------------------------------------------------

    async def initiate_call(
        self,
        channel_id: int,
        message: str,
        voice: str | None = None,
    ) -> dict[str, Any]:
        """Join a voice channel, speak an opening message, and listen for a reply.

        Args:
            channel_id: Discord voice channel ID to join.
            message: Opening message to speak to the user.

        Returns:
            dict with keys:
                call_id (str): UUID of the new session.
                transcript (str): STT transcript of the user's reply.
        """
        log.info("Initiating call on channel %d: %r", channel_id, message)

        # Join the voice channel (runs in the bot's event loop)
        voice_client: discord.VoiceClient = self._runner.run_coroutine(
            self._runner.bot.join_voice_channel(channel_id)
        )

        guild_id: int = voice_client.guild.id

        # Optionally grab a text channel for transcripts
        text_channel = self._runner.run_coroutine(
            self._runner.bot.get_text_channel(guild_id, channel_id)
        )

        call_id = str(uuid.uuid4())
        session = CallSession(
            call_id=call_id,
            channel_id=channel_id,
            guild_id=guild_id,
            voice_client=voice_client,
            text_channel=text_channel,
            started_at=time.monotonic(),
        )
        self._sessions[call_id] = session

        # Speak the opening message
        await self._tts_speak(voice_client, message, voice=voice)
        session.conversation_history.append({"role": "assistant", "content": message})
        self._post_to_text_channel(session, "assistant", message)

        # Listen for the user's reply
        transcript = await self._stt_listen(voice_client, session)
        session.conversation_history.append({"role": "user", "content": transcript})
        self._post_to_text_channel(session, "user", transcript)

        log.info("Call %s initiated; transcript: %r", call_id, transcript)
        return {"call_id": call_id, "transcript": transcript}

    async def continue_call(
        self,
        call_id: str,
        message: str,
        voice: str | None = None,
    ) -> dict[str, Any]:
        """Speak a message to the user and listen for their response.

        Args:
            call_id: Active session identifier returned by initiate_call.
            message: Message to speak.

        Returns:
            dict with key:
                transcript (str): STT transcript of the user's reply.

        Raises:
            KeyError: If call_id does not map to an active session.
        """
        session = self._get_session(call_id)
        log.info("Continuing call %s: %r", call_id, message)

        await self._tts_speak(session.voice_client, message, voice=voice)
        session.conversation_history.append({"role": "assistant", "content": message})
        self._post_to_text_channel(session, "assistant", message)

        transcript = await self._stt_listen(session.voice_client, session)
        session.conversation_history.append({"role": "user", "content": transcript})
        self._post_to_text_channel(session, "user", transcript)

        return {"transcript": transcript}

    async def speak_to_user(
        self,
        call_id: str,
        message: str,
        voice: str | None = None,
    ) -> dict[str, Any]:
        """Speak a message to the user without waiting for a response.

        Useful for one-way announcements or notifications during an active call.

        Args:
            call_id: Active session identifier.
            message: Message to speak.

        Returns:
            dict with key:
                status (str): "ok" on success.

        Raises:
            KeyError: If call_id does not map to an active session.
        """
        session = self._get_session(call_id)
        log.info("Speaking to user on call %s: %r", call_id, message)

        await self._tts_speak(session.voice_client, message, voice=voice)
        session.conversation_history.append({"role": "assistant", "content": message})
        self._post_to_text_channel(session, "assistant", message)

        return {"status": "ok"}

    async def end_call(
        self,
        call_id: str,
        message: str,
        voice: str | None = None,
    ) -> dict[str, Any]:
        """Speak a farewell message, leave the voice channel, and clean up.

        Args:
            call_id: Active session identifier.
            message: Farewell message to speak before disconnecting.

        Returns:
            dict with key:
                duration_seconds (float): Wall-clock duration of the call.

        Raises:
            KeyError: If call_id does not map to an active session.
        """
        session = self._get_session(call_id)
        log.info("Ending call %s with message: %r", call_id, message)

        await self._tts_speak(session.voice_client, message, voice=voice)
        session.conversation_history.append({"role": "assistant", "content": message})
        self._post_to_text_channel(session, "assistant", message)

        duration = time.monotonic() - session.started_at

        # Leave the voice channel via the bot's event loop
        self._runner.run_coroutine(
            self._runner.bot.leave_voice_channel(session.channel_id)
        )

        del self._sessions[call_id]
        log.info("Call %s ended; duration=%.1fs", call_id, duration)
        return {"duration_seconds": round(duration, 2)}

    # ------------------------------------------------------------------
    # Correction management
    # ------------------------------------------------------------------

    def add_correction(
        self, wrong: str, right: str, user_id: str = "default"
    ) -> dict[str, Any]:
        """Store a word-level STT correction, persisted to disk.

        When the STT engine consistently mishears a word (e.g. a proper noun),
        register it here so transcripts are automatically corrected via the
        LLM-based correction pipeline.

        Args:
            wrong: The word as incorrectly transcribed by the STT engine.
            right: The correct word to substitute.
            user_id: Discord user ID (defaults to "default" for server-wide).

        Returns:
            dict with key:
                status (str): "ok" on success.
        """
        self._stt.correction_manager.add_correction(user_id, wrong, right)
        return {"status": "ok"}

    def list_corrections(self, user_id: str = "default") -> dict[str, Any]:
        """Return all stored STT corrections for a user.

        Args:
            user_id: Discord user ID (defaults to "default" for server-wide).

        Returns:
            dict with key:
                corrections (dict[str, str]): Mapping of wrong -> right.
        """
        return {"corrections": self._stt.correction_manager.list_corrections(user_id)}
