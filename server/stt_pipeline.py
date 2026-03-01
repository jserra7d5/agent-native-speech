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
        log.info("STT listen() called for user=%s (id=%s)", user, user_id)
        sink = UserAudioSink(target_user=user)
        self._vad.reset()

        # Attach the sink to the voice client
        log.info("Attaching audio sink to voice client (type=%s)", type(voice_client).__name__)
        try:
            voice_client.listen(sink)
            log.info("Audio sink attached successfully")
        except Exception:
            log.exception("Failed to attach audio sink — voice_recv may not be available")
            return ""

        speech_audio: np.ndarray | None = None
        start_time = time.monotonic()

        try:
            speech_audio = await self._wait_for_speech(sink, timeout_s, start_time)
        finally:
            # Always detach the sink
            log.info("Detaching audio sink (elapsed=%.1fs, got_audio=%s)",
                     time.monotonic() - start_time, speech_audio is not None)
            try:
                voice_client.stop_listening()
            except Exception:
                pass
            sink.cleanup()

        # DEBUG: Always save received audio to a WAV file for analysis
        if hasattr(self, '_debug_all_audio') and self._debug_all_audio is not None:
            try:
                import soundfile as sf
                debug_path = "/tmp/voice-agent-debug.wav"
                sf.write(debug_path, self._debug_all_audio, 16000)
                log.info("DEBUG: Saved %d samples (%.2fs) of raw received audio to %s",
                         len(self._debug_all_audio), len(self._debug_all_audio) / 16000, debug_path)
            except Exception:
                log.exception("Failed to save debug audio")
            self._debug_all_audio = None

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
        poll_count = 0
        audio_chunks_received = 0
        total_samples = 0
        debug_chunks: list[np.ndarray] = []
        last_audio_time: float | None = None
        # When Discord stops sending packets (user stops speaking), feed
        # synthetic silence to the VAD so the silence counter can trigger.
        _SILENCE_INJECT_DELAY_S = 0.3  # start injecting after 300ms of no audio
        _SILENCE_CHUNK = np.zeros(512, dtype=np.float32)  # one VAD window of silence
        try:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_s:
                    log.info("STT timeout after %.1fs (%d polls, %d audio chunks, %d total samples)",
                             elapsed, poll_count, audio_chunks_received, total_samples)
                    return None

                poll_count += 1

                # Drain whatever audio has accumulated in the sink
                audio = sink.get_audio()
                if audio is not None and len(audio) > 0:
                    audio_chunks_received += 1
                    total_samples += len(audio)
                    last_audio_time = time.monotonic()
                    debug_chunks.append(audio.copy())
                    if audio_chunks_received <= 3 or audio_chunks_received % 20 == 0:
                        log.debug("STT got audio chunk #%d: %d samples (%.3fs)",
                                  audio_chunks_received, len(audio), len(audio) / 16000)
                    event = self._vad.process_chunk(audio)
                    if event is not None and event.type == "end" and event.audio is not None:
                        log.info("VAD detected speech end after %.1fs (%d chunks)",
                                 elapsed, audio_chunks_received)
                        return event.audio
                elif last_audio_time is not None:
                    # No audio arrived this poll. If we're in the SPEAKING
                    # state and enough time has passed, inject silence so the
                    # VAD's silence counter can accumulate and trigger end.
                    gap = time.monotonic() - last_audio_time
                    if gap >= _SILENCE_INJECT_DELAY_S:
                        event = self._vad.process_chunk(_SILENCE_CHUNK)
                        if event is not None and event.type == "end" and event.audio is not None:
                            log.info("VAD detected speech end (silence inject) after %.1fs (%d chunks)",
                                     elapsed, audio_chunks_received)
                            return event.audio

                # Log periodically if no audio is arriving
                if poll_count == 20:
                    log.info("STT: 20 polls done, %d audio chunks received so far", audio_chunks_received)
                elif poll_count == 100:
                    log.info("STT: 100 polls done, %d audio chunks received so far", audio_chunks_received)

                # Yield to the event loop
                await asyncio.sleep(_POLL_INTERVAL_S)
        finally:
            # Always save debug audio (even on cancellation)
            if debug_chunks:
                self._debug_all_audio = np.concatenate(debug_chunks)
                log.info("Saved %d debug audio chunks (%d total samples) for analysis",
                         len(debug_chunks), total_samples)

    def warmup(self) -> None:
        """Pre-load and warm the Whisper model."""
        self._transcriber.warmup()

    def unload(self) -> None:
        """Release GPU/CPU resources held by the pipeline models."""
        self._transcriber.unload()
        log.info("STT pipeline unloaded")
