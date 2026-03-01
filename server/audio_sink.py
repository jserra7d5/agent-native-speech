"""Audio sink for receiving PCM audio from Discord voice channels.

Implements discord.ext.voice_recv.AudioSink to capture per-user audio,
resample it from 48kHz stereo to 16kHz mono, and buffer it for downstream
consumption by Whisper STT and Silero VAD.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import discord
import librosa
import numpy as np

if TYPE_CHECKING:
    import discord.ext.voice_recv as voice_recv

log = logging.getLogger(__name__)

# Discord audio constants
DISCORD_SAMPLE_RATE: int = 48_000
DISCORD_CHANNELS: int = 2
DISCORD_SAMPLE_WIDTH: int = 2  # bytes per sample (int16)
DISCORD_FRAME_MS: int = 20
DISCORD_FRAME_BYTES: int = (
    DISCORD_SAMPLE_RATE // (1000 // DISCORD_FRAME_MS)
    * DISCORD_CHANNELS
    * DISCORD_SAMPLE_WIDTH
)  # 3840 bytes

# Output audio constants (Whisper / Silero VAD)
OUTPUT_SAMPLE_RATE: int = 16_000


class UserAudioSink:
    """Receives decoded PCM audio frames for a single Discord user.

    Discord delivers 48kHz stereo 16-bit PCM frames (20 ms each, 3840 bytes).
    Frames are resampled to 16kHz mono float32 and accumulated in a ring buffer
    capped at *max_duration_s* seconds.

    Thread safety
    -------------
    ``write()`` is invoked from discord.py's internal voice-receive thread.
    ``get_audio()``, ``reset()``, and ``is_receiving()`` may be called from any
    other thread.  A :class:`threading.Lock` protects all access to the shared
    buffer and state flag.

    Parameters
    ----------
    member:
        The :class:`discord.Member` whose audio should be captured.  Frames
        from any other user are silently dropped.
    max_duration_s:
        Maximum number of seconds of audio to retain in the buffer.  Older
        samples are dropped when the buffer would exceed this limit.
        Defaults to 30 seconds.
    """

    def __init__(
        self,
        member: discord.Member,
        max_duration_s: float = 30.0,
    ) -> None:
        self.member = member
        self.max_duration_s = max_duration_s

        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._total_samples: int = 0
        self._receiving: bool = False

        # Pre-compute the max number of output samples we will retain.
        self._max_output_samples: int = int(OUTPUT_SAMPLE_RATE * max_duration_s)

        log.debug(
            "UserAudioSink created for member=%s max_duration_s=%.1f",
            member,
            max_duration_s,
        )

    # ------------------------------------------------------------------
    # AudioSink protocol
    # ------------------------------------------------------------------

    def wants_opus(self) -> bool:
        """Return False — we want decoded PCM, not raw Opus packets."""
        return False

    def write(self, user: discord.User | discord.Member, data: "voice_recv.VoiceData") -> None:
        """Receive a decoded PCM frame from *user* and buffer it.

        Called from discord.py's voice-receive thread.  Frames from users
        other than ``self.member`` are ignored.

        Parameters
        ----------
        user:
            The Discord user who produced this frame.
        data:
            A ``VoiceData`` object whose ``.pcm`` attribute is raw PCM bytes
            (48kHz, stereo, int16, 20 ms).
        """
        if user.id != self.member.id:
            return

        pcm_bytes: bytes = data.pcm
        if not pcm_bytes:
            return

        # Decode bytes → int16 numpy array, then normalise to float32 [-1, 1].
        int16_samples = np.frombuffer(pcm_bytes, dtype=np.int16)

        # Reshape to (num_frames, 2) — interleaved stereo — then average
        # channels to produce a mono signal.
        if int16_samples.size % DISCORD_CHANNELS != 0:
            log.warning(
                "Received PCM frame with unexpected byte length %d; skipping",
                len(pcm_bytes),
            )
            return

        stereo = int16_samples.reshape(-1, DISCORD_CHANNELS).astype(np.float32)
        mono = stereo.mean(axis=1)  # average left + right

        # Normalise int16 range to float32 [-1.0, 1.0].
        mono /= 32768.0

        # Resample 48kHz → 16kHz.  librosa.resample expects shape (channels, samples)
        # or a 1-D array; we pass a 1-D mono signal.
        resampled: np.ndarray = librosa.resample(
            mono,
            orig_sr=DISCORD_SAMPLE_RATE,
            target_sr=OUTPUT_SAMPLE_RATE,
            res_type="soxr_hq",
        )

        with self._lock:
            self._chunks.append(resampled)
            self._total_samples += resampled.size
            self._receiving = True

            # Evict oldest chunks if the buffer exceeds the duration cap.
            while self._total_samples > self._max_output_samples and self._chunks:
                evicted = self._chunks.pop(0)
                self._total_samples -= evicted.size

    def cleanup(self) -> None:
        """Called by discord.py when the sink is detached.  Clears state."""
        log.debug("UserAudioSink.cleanup() called for member=%s", self.member)
        self.reset()

    # ------------------------------------------------------------------
    # Public consumer API
    # ------------------------------------------------------------------

    def get_audio(self) -> np.ndarray | None:
        """Return the accumulated 16kHz mono float32 buffer and clear it.

        Returns ``None`` if no audio has been received since the last call.

        Returns
        -------
        numpy.ndarray or None
            A 1-D float32 array at 16 kHz, or ``None`` when the buffer is empty.
        """
        with self._lock:
            if not self._chunks:
                return None

            audio = np.concatenate(self._chunks, axis=0)
            self._chunks.clear()
            self._total_samples = 0
            self._receiving = False

            log.debug(
                "get_audio() returning %.3f s of audio (%d samples) for member=%s",
                audio.size / OUTPUT_SAMPLE_RATE,
                audio.size,
                self.member,
            )
            return audio

    def is_receiving(self) -> bool:
        """Return True if audio frames have been written since the last reset.

        This reflects whether audio arrived *at some point* since the buffer
        was last cleared — it does not indicate real-time activity.  For
        real-time silence detection, use a VAD on the samples returned by
        :meth:`get_audio`.
        """
        with self._lock:
            return self._receiving

    def reset(self) -> None:
        """Discard all buffered audio and reset the receiving flag."""
        with self._lock:
            self._chunks.clear()
            self._total_samples = 0
            self._receiving = False
        log.debug("UserAudioSink.reset() called for member=%s", self.member)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def buffered_duration_s(self) -> float:
        """Seconds of audio currently held in the buffer (approximate)."""
        with self._lock:
            return self._total_samples / OUTPUT_SAMPLE_RATE


class MultiUserAudioSink:
    """An AudioSink that manages a :class:`UserAudioSink` per connected member.

    Useful when you want to capture audio from every user in a channel without
    knowing the member set in advance.  A ``UserAudioSink`` is created lazily
    on the first frame received from each user.

    Parameters
    ----------
    max_duration_s:
        Forwarded to each per-user :class:`UserAudioSink`.
    """

    def __init__(self, max_duration_s: float = 30.0) -> None:
        self.max_duration_s = max_duration_s
        self._lock = threading.Lock()
        self._sinks: dict[int, UserAudioSink] = {}

    def wants_opus(self) -> bool:
        """Return False — we want decoded PCM."""
        return False

    def write(self, user: discord.User | discord.Member, data: "voice_recv.VoiceData") -> None:
        """Route an incoming PCM frame to the appropriate per-user sink."""
        with self._lock:
            if user.id not in self._sinks:
                if not isinstance(user, discord.Member):
                    log.debug(
                        "Received audio from non-Member user %s; skipping sink creation",
                        user,
                    )
                    return
                log.info("Creating UserAudioSink for new member=%s", user)
                self._sinks[user.id] = UserAudioSink(
                    member=user,
                    max_duration_s=self.max_duration_s,
                )
            sink = self._sinks[user.id]

        sink.write(user, data)

    def get_sink(self, member: discord.Member) -> UserAudioSink | None:
        """Return the :class:`UserAudioSink` for *member*, or ``None``."""
        with self._lock:
            return self._sinks.get(member.id)

    def cleanup(self) -> None:
        """Detach all per-user sinks."""
        with self._lock:
            for sink in self._sinks.values():
                sink.cleanup()
            self._sinks.clear()
