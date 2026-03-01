"""Full STT pipeline: AudioSink → VAD → Whisper → Correction.

Orchestrates the speech-to-text flow for a single listen() call:
  1. Attach an AudioSink to the voice client to receive per-user audio
  2. Feed audio chunks through Silero VAD to detect speech boundaries
  3. When speech ends, transcribe the accumulated buffer with Whisper
  4. Apply LLM-based corrections using the user's correction dictionary
  5. Return the corrected transcript
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from server.audio_sink import UserAudioSink
from server.config import Config
from server.correction import CorrectionManager
from server.transcriber import Transcriber
from server.vad import SpeechDetector, SpeechEvent

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)

# How often we drain the audio sink and feed it to the VAD (seconds)
_POLL_INTERVAL_S: float = 0.05  # 50ms — ~25 VAD windows per poll


class STTPipeline:
    """Manages the shared STT resources (VAD model, Whisper model, correction manager).

    Create one instance at server startup. Call ``listen()`` for each turn.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._vad = SpeechDetector(config.vad)
        self._transcriber = Transcriber(config.stt)
        self._corrections = CorrectionManager(config.correction, config.anthropic_api_key)

    @property
    def correction_manager(self) -> CorrectionManager:
        """Expose the correction manager for add/list/remove operations."""
        return self._corrections

    async def listen(
        self,
        voice_client: discord.VoiceClient,
        user: discord.Member | discord.User,
        user_id: str,
        custom_vocab: list[str] | None = None,
        timeout_s: float = 60.0,
    ) -> str:
        """Listen for a single utterance and return the corrected transcript.

        Blocks until the user speaks and then stops speaking (silence detected),
        or until ``timeout_s`` elapses with no speech.

        Args:
            voice_client: The connected discord.py VoiceClient (must be a
                VoiceRecvClient from discord-ext-voice-recv).
            user: The Discord user to listen to (filters out other speakers).
            user_id: String identifier for loading the user's corrections.
            custom_vocab: Optional list of domain terms to bias Whisper toward.
            timeout_s: Maximum seconds to wait for speech before giving up.

        Returns:
            The corrected transcript string, or an empty string if no speech
            was detected within the timeout.
        """
        sink = UserAudioSink(target_user=user)
        self._vad.reset()

        # Attach the sink to the voice client
        try:
            voice_client.listen(sink)
        except Exception:
            log.exception("Failed to attach audio sink — voice_recv may not be available")
            return ""

        speech_audio: np.ndarray | None = None
        start_time = time.monotonic()

        try:
            speech_audio = await self._wait_for_speech(sink, timeout_s, start_time)
        finally:
            # Always detach the sink
            try:
                voice_client.stop_listening()
            except Exception:
                pass
            sink.cleanup()

        if speech_audio is None or len(speech_audio) == 0:
            log.info("No speech detected within %.1fs timeout", timeout_s)
            return ""

        duration = len(speech_audio) / 16_000
        log.info("Speech captured: %.2fs of audio", duration)

        # Transcribe
        corrections = self._corrections.get_corrections(user_id)
        initial_prompt = self._transcriber.build_initial_prompt(
            custom_vocab or [], corrections
        )
        result = self._transcriber.transcribe(speech_audio, initial_prompt=initial_prompt)
        raw_text = result.text
        log.info("Raw transcript: %r", raw_text)

        if not raw_text.strip():
            return ""

        # Apply LLM corrections
        corrected = await self._corrections.correct(raw_text, user_id)
        if corrected != raw_text:
            log.info("Corrected transcript: %r", corrected)

        return corrected

    async def _wait_for_speech(
        self,
        sink: UserAudioSink,
        timeout_s: float,
        start_time: float,
    ) -> np.ndarray | None:
        """Poll the audio sink and VAD until a complete utterance is detected."""
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_s:
                return None

            # Drain whatever audio has accumulated in the sink
            audio = sink.get_audio()
            if audio is not None and len(audio) > 0:
                event = self._vad.process_chunk(audio)
                if event is not None and event.type == "end" and event.audio is not None:
                    return event.audio

            # Yield to the event loop
            await asyncio.sleep(_POLL_INTERVAL_S)

    def warmup(self) -> None:
        """Pre-load and warm the Whisper model."""
        self._transcriber.warmup()

    def unload(self) -> None:
        """Release GPU/CPU resources held by the pipeline models."""
        self._transcriber.unload()
        log.info("STT pipeline unloaded")
