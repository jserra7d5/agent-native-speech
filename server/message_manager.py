"""Text-channel message manager for message-only mode.

Implements the same call lifecycle as CallManager (initiate_call,
continue_call, speak_to_user, end_call) but over Discord text messages
instead of voice.  The MCP tools are identical — the agent doesn't
know it's not a voice call.

Reply detection: accepts any message from the owning user in the
session's text channel.  Voice message attachments (.ogg) are decoded
via ffmpeg and transcribed through the STT pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import discord
import numpy as np

if TYPE_CHECKING:
    from server.discord_bot import BotRunner
    from server.stt_pipeline import STTPipeline

log = logging.getLogger(__name__)

# Default timeout for waiting for a user reply in message mode (seconds).
# Much longer than voice mode's 60s because typing takes longer.
_DEFAULT_REPLY_TIMEOUT_S: float = 300.0


@dataclass
class MessageSession:
    """Represents a single active text-message session.

    Attributes:
        call_id: Unique identifier (UUID string) for this session.
        channel_id: Discord TEXT channel ID.
        guild_id: Discord guild (server) ID.
        text_channel: The discord.TextChannel object.
        last_bot_message: Most recent message sent by the bot in this session.
        started_at: Monotonic timestamp when the session was created.
        conversation_history: Ordered list of message dicts with keys
            "role" ("user" | "assistant") and "content" (str).
        owning_user_id: Discord user ID who initiated the session.
    """

    call_id: str
    channel_id: int
    guild_id: int
    text_channel: discord.TextChannel
    last_bot_message: discord.Message | None = None
    started_at: float = field(default_factory=time.monotonic)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    owning_user_id: str = ""


class MessageManager:
    """Bridges MCP tools to Discord text channel messaging.

    Parallel to CallManager but operates over text messages instead of
    voice.  Uses asyncio.Future objects to wait for user replies from
    the Discord bot thread.
    """

    def __init__(
        self,
        bot_runner: BotRunner,
        stt_pipeline: STTPipeline | None = None,
        reply_timeout_s: float = _DEFAULT_REPLY_TIMEOUT_S,
    ) -> None:
        self._runner = bot_runner
        self._stt = stt_pipeline
        self._reply_timeout_s = reply_timeout_s
        # Keyed by call_id
        self._sessions: dict[str, MessageSession] = {}
        # Pending reply futures: call_id -> (Future, event_loop)
        self._pending_replies: dict[str, tuple[asyncio.Future[str], asyncio.AbstractEventLoop]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self, call_id: str) -> MessageSession:
        """Return the session or raise KeyError."""
        try:
            return self._sessions[call_id]
        except KeyError:
            raise KeyError(f"No active message session with id '{call_id}'") from None

    def _find_session_for_message(self, message: discord.Message) -> MessageSession | None:
        """Find the message session that should receive this Discord message.

        Matches by: same text channel AND message author is the owning user.
        """
        author_id = str(message.author.id)
        channel_id = message.channel.id
        for session in self._sessions.values():
            if session.channel_id == channel_id and session.owning_user_id == author_id:
                return session
        return None

    async def _send_message(
        self,
        session: MessageSession,
        content: str,
    ) -> discord.Message:
        """Send a text message in the session's channel via the bot thread.

        Returns the sent discord.Message object.
        """
        async def _send() -> discord.Message:
            return await session.text_channel.send(content)

        msg = self._runner.run_coroutine(_send())
        session.last_bot_message = msg
        return msg

    async def _wait_for_reply(self, call_id: str) -> str:
        """Wait for the user to reply in the text channel.

        Creates a Future that will be resolved by handle_discord_message()
        when a matching message arrives from the owning user.

        Returns the text content (or transcribed voice message).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_replies[call_id] = (future, loop)

        try:
            transcript = await asyncio.wait_for(
                future, timeout=self._reply_timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning(
                "Reply timeout (%.0fs) for message session %s",
                self._reply_timeout_s, call_id,
            )
            transcript = ""
        finally:
            self._pending_replies.pop(call_id, None)

        return transcript

    async def _decode_voice_message_from_bytes(self, ogg_data: bytes) -> str:
        """Transcribe a voice message from raw OGG bytes.

        Decodes to 16kHz mono float32 via ffmpeg, then runs through
        the STT pipeline's transcribe_and_correct method.
        """
        if self._stt is None:
            log.warning("STT pipeline not available for voice message transcription")
            return "[voice message - transcription unavailable]"

        try:
            # Decode ogg to raw PCM via ffmpeg
            loop = asyncio.get_running_loop()
            audio = await loop.run_in_executor(None, self._ffmpeg_decode, ogg_data)

            if audio is None or len(audio) == 0:
                log.warning("Failed to decode voice message audio")
                return "[voice message - decode failed]"

            # Transcribe through the STT pipeline
            transcript = await self._stt._transcribe_and_correct(
                audio, user_id="default",
            )
            log.info("Voice message transcribed: %r", transcript)
            return transcript if transcript.strip() else "[voice message - no speech detected]"

        except Exception:
            log.exception("Error processing voice message")
            return "[voice message - processing error]"

    @staticmethod
    def _ffmpeg_decode(ogg_data: bytes) -> np.ndarray | None:
        """Decode OGG audio to 16kHz mono float32 numpy array via ffmpeg."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
                tmp.write(ogg_data)
                tmp.flush()

                result = subprocess.run(
                    [
                        "ffmpeg", "-i", tmp.name,
                        "-f", "f32le",       # raw float32 little-endian
                        "-acodec", "pcm_f32le",
                        "-ar", "16000",       # 16kHz
                        "-ac", "1",           # mono
                        "pipe:1",
                    ],
                    capture_output=True,
                    timeout=30,
                )

                if result.returncode != 0:
                    log.error("ffmpeg decode failed: %s", result.stderr.decode(errors="replace"))
                    return None

                audio = np.frombuffer(result.stdout, dtype=np.float32)
                log.info("Decoded voice message: %d samples (%.2fs)", len(audio), len(audio) / 16000)
                return audio

        except subprocess.TimeoutExpired:
            log.error("ffmpeg decode timed out")
            return None
        except Exception:
            log.exception("ffmpeg decode error")
            return None

    # ------------------------------------------------------------------
    # Discord message handler (called from bot's on_message)
    # ------------------------------------------------------------------

    def handle_discord_message(self, message: discord.Message) -> None:
        """Process an incoming Discord message for active message sessions.

        Called from the Discord bot thread's on_message event. If the
        message matches an active session with a pending reply, resolves
        the Future with the message content.

        For voice message attachments, schedules async transcription and
        resolves the future when transcription completes.
        """
        if message.author.bot:
            return

        session = self._find_session_for_message(message)
        if session is None:
            return

        call_id = session.call_id
        pending = self._pending_replies.get(call_id)
        if pending is None:
            return

        future, loop = pending

        # Check for voice message attachments (Discord voice messages are .ogg)
        voice_attachment = None
        for att in message.attachments:
            if att.filename and att.filename.endswith(".ogg"):
                voice_attachment = att
                break
            # Discord voice messages have the is_voice_message flag
            if hasattr(att, "is_voice_message") and att.is_voice_message:
                voice_attachment = att
                break

        if voice_attachment is not None:
            # Download the attachment in the bot's event loop (we're already
            # in the bot thread), then schedule transcription on the MCP loop.
            bot_loop = asyncio.get_event_loop()

            async def _download_and_transcribe() -> None:
                try:
                    # Download runs in the bot loop
                    ogg_data = await voice_attachment.read()
                    # Transcription can run on the MCP loop
                    text = await self._decode_voice_message_from_bytes(ogg_data)
                    if not future.done():
                        future.set_result(text)
                except Exception as exc:
                    if not future.done():
                        future.set_result(f"[voice message error: {exc}]")

            # Schedule the download in the bot's loop (where we are now)
            asyncio.ensure_future(_download_and_transcribe(), loop=bot_loop)
        else:
            # Plain text message
            content = message.content or ""
            loop.call_soon_threadsafe(future.set_result, content)

    # ------------------------------------------------------------------
    # Public call API (mirrors CallManager)
    # ------------------------------------------------------------------

    async def initiate_call(
        self,
        channel_id: int,
        message: str,
        user_id: str = "",
    ) -> dict[str, Any]:
        """Start a message session in a text channel.

        Sends the opening message and waits for the user's reply.

        Args:
            channel_id: Discord text channel ID.
            message: Opening message to send.
            user_id: Discord user ID of the session owner.

        Returns:
            dict with call_id and transcript.
        """
        log.info("Initiating message session on channel %d: %r", channel_id, message)

        # Fetch the text channel from the bot
        async def _get_channel() -> discord.TextChannel:
            await self._runner.bot.wait_until_bot_ready()
            channel = self._runner.bot.get_channel(channel_id)
            if channel is None:
                raise ValueError(f"Channel {channel_id} not found")
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise ValueError(
                    f"Channel {channel_id} is not a text channel (got {type(channel).__name__})"
                )
            return channel

        text_channel = self._runner.run_coroutine(_get_channel())
        guild_id = text_channel.guild.id

        call_id = str(uuid.uuid4())
        session = MessageSession(
            call_id=call_id,
            channel_id=channel_id,
            guild_id=guild_id,
            text_channel=text_channel,
            owning_user_id=user_id,
        )
        self._sessions[call_id] = session

        # Send opening message
        await self._send_message(session, message)
        session.conversation_history.append({"role": "assistant", "content": message})

        # Wait for user reply
        transcript = await self._wait_for_reply(call_id)
        session.conversation_history.append({"role": "user", "content": transcript})

        log.info("Message session %s initiated; transcript: %r", call_id, transcript)
        return {"call_id": call_id, "transcript": transcript}

    async def continue_call(
        self,
        call_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Send a message and wait for the user's reply.

        Args:
            call_id: Active session identifier.
            message: Message to send.

        Returns:
            dict with transcript key.
        """
        session = self._get_session(call_id)
        log.info("Continuing message session %s: %r", call_id, message)

        await self._send_message(session, message)
        session.conversation_history.append({"role": "assistant", "content": message})

        transcript = await self._wait_for_reply(call_id)
        session.conversation_history.append({"role": "user", "content": transcript})

        return {"transcript": transcript}

    async def speak_to_user(
        self,
        call_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Send a one-way message without waiting for a reply.

        Args:
            call_id: Active session identifier.
            message: Message to send.

        Returns:
            dict with status key.
        """
        session = self._get_session(call_id)
        log.info("Sending message on session %s: %r", call_id, message)

        await self._send_message(session, message)
        session.conversation_history.append({"role": "assistant", "content": message})

        return {"status": "ok"}

    async def end_call(
        self,
        call_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Send a farewell message and clean up the session.

        Args:
            call_id: Active session identifier.
            message: Farewell message.

        Returns:
            dict with duration_seconds key.
        """
        session = self._get_session(call_id)
        log.info("Ending message session %s with: %r", call_id, message)

        await self._send_message(session, message)
        session.conversation_history.append({"role": "assistant", "content": message})

        duration = time.monotonic() - session.started_at
        del self._sessions[call_id]

        # Cancel any pending reply future
        pending = self._pending_replies.pop(call_id, None)
        if pending is not None:
            future, loop = pending
            if not future.done():
                loop.call_soon_threadsafe(future.cancel)

        log.info("Message session %s ended; duration=%.1fs", call_id, duration)
        return {"duration_seconds": round(duration, 2)}
