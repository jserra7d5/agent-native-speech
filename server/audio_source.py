"""Discord AudioSource implementation for TTS audio playback.

Bridges the gap between the Qwen3-TTS engine (24kHz mono float32) and
Discord's required format (48kHz stereo 16-bit PCM, 3840 bytes per 20ms frame).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from typing import Optional

import discord
import librosa
import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

# Discord voice constants (from discord-ext-voice-recv research)
DISCORD_SAMPLE_RATE: int = 48_000  # Hz — fixed by Discord, cannot be changed
DISCORD_CHANNELS: int = 2          # Stereo
DISCORD_SAMPLE_WIDTH: int = 2      # Bytes per sample (int16)
DISCORD_FRAME_MS: float = 0.020    # 20 ms per frame

# Derived: 48000 Hz × 2 ch × 2 bytes × 0.020 s = 3840 bytes
DISCORD_FRAME_BYTES: int = int(
    DISCORD_SAMPLE_RATE * DISCORD_CHANNELS * DISCORD_SAMPLE_WIDTH * DISCORD_FRAME_MS
)  # 3840

# TTS engine output format
TTS_SAMPLE_RATE: int = 24_000  # Hz — Qwen3-TTS native rate


class TTSAudioSource(discord.AudioSource):
    """Plays synthesized TTS audio into a Discord voice channel.

    Accepts raw float32 audio from the TTS engine, performs all necessary
    format conversion in the constructor so that ``read()`` is a simple
    non-blocking slice operation safe to call from discord.py's audio thread.

    Conversion pipeline:
        float32 mono 24kHz  →  resample to 48kHz  →  duplicate to stereo
        →  clip to [-1, 1]  →  scale to int16  →  little-endian bytes

    When all frames have been consumed, a completion ``asyncio.Event`` is
    set so callers can ``await source.done`` to know when playback has
    finished.
    """

    def __init__(self, pcm_bytes: bytes) -> None:
        """Initialise from a pre-converted block of 48kHz stereo int16 PCM.

        Prefer the factory methods :meth:`from_audio` and :meth:`from_file`
        over calling this constructor directly.

        Args:
            pcm_bytes: Raw PCM bytes in Discord's expected format
                       (48kHz, stereo, 16-bit signed little-endian).
        """
        self._data: bytes = pcm_bytes
        self._offset: int = 0
        self._total_frames: int = len(pcm_bytes) // DISCORD_FRAME_BYTES

        # Event is set when the last frame has been read, giving callers a
        # clean way to await completion without polling the voice client.
        self.done: asyncio.Event = asyncio.Event()

        log.debug(
            "TTSAudioSource ready: %d bytes → %d frames (%.2f s)",
            len(pcm_bytes),
            self._total_frames,
            self._total_frames * DISCORD_FRAME_MS,
        )

    # ------------------------------------------------------------------
    # discord.AudioSource interface
    # ------------------------------------------------------------------

    def read(self) -> bytes:
        """Return exactly 3840 bytes (one 20ms Discord frame) of PCM audio.

        Returns an empty ``bytes`` object when all audio has been consumed,
        which signals discord.py to stop playback.  The :attr:`done` event
        is set at that point.

        This method is called from discord.py's internal audio thread; it
        must be fast and non-blocking.

        Returns:
            3840 bytes of 16-bit 48kHz stereo PCM, or ``b""`` at EOF.
        """
        remaining = len(self._data) - self._offset

        if remaining <= 0:
            # Already exhausted — ensure event is set even on repeated calls.
            if not self.done.is_set():
                self.done.set()
            return b""

        chunk = self._data[self._offset : self._offset + DISCORD_FRAME_BYTES]
        self._offset += DISCORD_FRAME_BYTES

        if len(chunk) < DISCORD_FRAME_BYTES:
            # Last partial frame: zero-pad to exactly 3840 bytes so Discord
            # does not receive a malformed frame.
            chunk = chunk.ljust(DISCORD_FRAME_BYTES, b"\x00")
            self.done.set()
            log.debug("TTSAudioSource: last (padded) frame sent, playback complete")
        elif self._offset >= len(self._data):
            # Perfectly aligned last frame.
            self.done.set()
            log.debug("TTSAudioSource: last frame sent, playback complete")

        return chunk

    def is_opus(self) -> bool:
        """Return False — data is raw PCM, not Opus-encoded."""
        return False

    def cleanup(self) -> None:
        """Release the PCM buffer and signal completion.

        Called by discord.py when the voice client finishes or is stopped.
        Ensures :attr:`done` is always set even if playback is aborted.
        """
        self._data = b""
        self._offset = 0
        if not self.done.is_set():
            self.done.set()
        log.debug("TTSAudioSource cleaned up")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_frames(self) -> int:
        """Total number of 20ms frames in this source."""
        return self._total_frames

    @property
    def duration_seconds(self) -> float:
        """Total playback duration in seconds."""
        return self._total_frames * DISCORD_FRAME_MS

    @property
    def frames_remaining(self) -> int:
        """Number of 20ms frames not yet consumed by :meth:`read`."""
        consumed = self._offset // DISCORD_FRAME_BYTES
        return max(0, self._total_frames - consumed)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_audio(
        cls,
        audio: np.ndarray,
        sample_rate: int = TTS_SAMPLE_RATE,
    ) -> "TTSAudioSource":
        """Create a ``TTSAudioSource`` from a numpy float32 audio array.

        This is the primary entry point used by the TTS pipeline.

        Args:
            audio: 1-D numpy array of float32 samples in the range [-1, 1].
                   Mono (single channel) is expected; multi-channel input
                   is collapsed to mono by averaging across axis 0.
            sample_rate: Sample rate of ``audio`` in Hz.  Defaults to
                         24000 (Qwen3-TTS native rate).

        Returns:
            A fully initialised :class:`TTSAudioSource` ready for playback.

        Raises:
            ValueError: If ``audio`` is empty or has an unsupported dtype.
        """
        if audio.size == 0:
            raise ValueError("audio array must not be empty")

        # Ensure float32 for consistent arithmetic
        audio = audio.astype(np.float32)

        # Collapse multi-channel input to mono
        if audio.ndim == 2:
            log.debug(
                "from_audio: collapsing %d-channel audio to mono", audio.shape[0]
            )
            audio = audio.mean(axis=0)
        elif audio.ndim != 1:
            raise ValueError(
                f"audio must be 1-D (mono) or 2-D (channels × samples), "
                f"got shape {audio.shape}"
            )

        log.debug(
            "from_audio: %.3f s of audio at %d Hz (%d samples)",
            len(audio) / sample_rate,
            sample_rate,
            len(audio),
        )

        # Step 1: Resample to 48kHz if needed
        if sample_rate != DISCORD_SAMPLE_RATE:
            log.debug(
                "from_audio: resampling %d Hz → %d Hz", sample_rate, DISCORD_SAMPLE_RATE
            )
            audio = librosa.resample(
                audio,
                orig_sr=sample_rate,
                target_sr=DISCORD_SAMPLE_RATE,
            )
        else:
            log.debug("from_audio: already at %d Hz, skipping resample", DISCORD_SAMPLE_RATE)

        # Step 2: Mono → stereo (duplicate channel)
        # Result shape: (2, num_samples) → interleaved as L R L R …
        stereo = np.stack([audio, audio], axis=1)  # (num_samples, 2)

        # Step 3: Clip to [-1, 1] to prevent int16 overflow
        stereo = np.clip(stereo, -1.0, 1.0)

        # Step 4: Scale float32 → int16
        # np.int16 range: -32768 … 32767.  Multiply by 32767 (not 32768) to
        # keep headroom and avoid the single overflow point at exactly -1.0.
        int16_samples = (stereo * 32767).astype(np.int16)

        # Step 5: Serialise to little-endian bytes (Discord / PCM standard)
        pcm_bytes: bytes = int16_samples.tobytes()

        log.info(
            "from_audio: produced %.3f s of audio (%d bytes, %d frames)",
            len(audio) / DISCORD_SAMPLE_RATE,
            len(pcm_bytes),
            len(pcm_bytes) // DISCORD_FRAME_BYTES,
        )

        return cls(pcm_bytes)

    @classmethod
    def from_file(cls, path: str) -> "TTSAudioSource":
        """Create a ``TTSAudioSource`` by loading a WAV (or any soundfile-
        readable) audio file from disk.

        Primarily intended for testing and development.  The file may be at
        any sample rate and channel count; all necessary conversion is
        applied automatically via :meth:`from_audio`.

        Args:
            path: Filesystem path to the audio file.

        Returns:
            A fully initialised :class:`TTSAudioSource` ready for playback.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            RuntimeError: If the file cannot be read by soundfile.
        """
        log.debug("from_file: loading audio from %r", path)
        try:
            audio, file_sample_rate = sf.read(path, dtype="float32", always_2d=False)
        except Exception as exc:
            raise RuntimeError(f"Failed to load audio file {path!r}: {exc}") from exc

        log.info(
            "from_file: loaded %r — %d samples at %d Hz",
            path,
            len(audio) if audio.ndim == 1 else audio.shape[0],
            file_sample_rate,
        )

        # soundfile returns (samples,) for mono or (samples, channels) for
        # multi-channel.  Transpose multi-channel so from_audio receives
        # (channels, samples) as documented.
        if audio.ndim == 2:
            audio = audio.T  # (samples, ch) → (ch, samples)

        return cls.from_audio(audio, sample_rate=file_sample_rate)


class StreamingAudioSource(discord.AudioSource):
    """Plays TTS audio segments back-to-back as they arrive from synthesis.

    Synthesis runs in a background thread and feeds converted PCM segments
    into this source via :meth:`add_segment`.  Discord's audio thread reads
    frames from :meth:`read` at a fixed 20 ms cadence.  When the reader
    catches up with the writer (no buffered data yet, synthesis still in
    progress) a silent frame is returned so playback does not stall.

    Typical usage::

        source = StreamingAudioSource()

        def _worker():
            for audio, sr in tts_engine.synthesize_streamed(text):
                source.add_segment(audio, sr)
            source.finish()

        loop.run_in_executor(None, _worker)
        voice_client.play(source)
        await source.done.wait()
    """

    def __init__(self) -> None:
        self._segments: collections.deque[bytes] = collections.deque()
        self._current: bytes = b""
        self._offset: int = 0
        self._finished: bool = False  # set by finish(); no more segments coming
        self._lock = threading.Lock()
        self.done: asyncio.Event = asyncio.Event()

        log.debug("StreamingAudioSource created")

    # ------------------------------------------------------------------
    # Producer interface (called from synthesis thread)
    # ------------------------------------------------------------------

    def add_segment(self, audio: np.ndarray, sample_rate: int) -> None:
        """Convert *audio* to Discord PCM and enqueue it for playback.

        Called from the synthesis background thread each time a sentence
        chunk finishes rendering.

        Args:
            audio: 1-D float32 NumPy array of mono audio samples.
            sample_rate: Sample rate of *audio* in Hz (typically 24000).
        """
        source = TTSAudioSource.from_audio(audio, sample_rate)
        with self._lock:
            self._segments.append(source._data)
        log.debug(
            "StreamingAudioSource: queued segment (%d bytes, %d frames)",
            len(source._data),
            len(source._data) // DISCORD_FRAME_BYTES,
        )

    def finish(self) -> None:
        """Signal that no more segments will be added.

        Must be called by the synthesis worker (even on error, ideally in a
        ``finally`` block) so the audio thread knows when to stop.
        """
        with self._lock:
            self._finished = True
        log.debug("StreamingAudioSource: finish() called")

    # ------------------------------------------------------------------
    # discord.AudioSource interface (called from Discord audio thread)
    # ------------------------------------------------------------------

    def read(self) -> bytes:
        """Return the next 3840-byte Discord frame.

        Pulls from the current in-progress segment, advancing to the next
        queued segment when the current one is exhausted.  Returns a frame
        of silence when no buffered data is available but synthesis is still
        ongoing.  Returns ``b""`` when all segments have been consumed and
        :meth:`finish` has been called, signalling end-of-stream to discord.py.

        This method is called from discord.py's audio thread; it is fast
        and non-blocking.

        Returns:
            3840 bytes of 16-bit 48kHz stereo PCM, a 3840-byte silence frame
            while waiting for the next segment, or ``b""`` at end-of-stream.
        """
        with self._lock:
            # Try to advance to the next buffered segment if the current one
            # is exhausted.
            remaining = len(self._current) - self._offset
            if remaining <= 0:
                if self._segments:
                    self._current = self._segments.popleft()
                    self._offset = 0
                    remaining = len(self._current)
                elif self._finished:
                    # All data consumed and synthesis is done.
                    if not self.done.is_set():
                        self.done.set()
                    return b""
                else:
                    # Synthesis still running but no buffered data yet — pad
                    # with silence to keep discord.py's audio thread ticking.
                    return b"\x00" * DISCORD_FRAME_BYTES

            chunk = self._current[self._offset : self._offset + DISCORD_FRAME_BYTES]
            self._offset += DISCORD_FRAME_BYTES

            if len(chunk) < DISCORD_FRAME_BYTES:
                # Last partial frame of this segment: zero-pad to a full frame.
                chunk = chunk.ljust(DISCORD_FRAME_BYTES, b"\x00")

            return chunk

    def is_opus(self) -> bool:
        """Return False — data is raw PCM, not Opus-encoded."""
        return False

    def cleanup(self) -> None:
        """Discard buffered data and signal completion.

        Called by discord.py when the voice client finishes or is stopped.
        Ensures :attr:`done` is always set even if playback is aborted.
        """
        with self._lock:
            self._segments.clear()
            self._current = b""
            self._offset = 0
            self._finished = True
        if not self.done.is_set():
            self.done.set()
        log.debug("StreamingAudioSource cleaned up")
