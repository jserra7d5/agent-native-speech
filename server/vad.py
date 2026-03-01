"""Voice Activity Detection (VAD) for Discord audio streams.

Wraps the Silero VAD model to detect speech start/end events in a 16kHz mono
float32 audio stream.  Designed to sit between ``UserAudioSink`` (which
delivers resampled chunks) and the Whisper STT engine.

Typical usage
-------------
::

    from server.config import VADConfig
    from server.vad import SpeechDetector, SpeechEvent

    detector = SpeechDetector(VADConfig())

    # Called repeatedly from the Discord voice-receive thread:
    event = detector.process_chunk(chunk)
    if event is not None:
        if event.type == "start":
            print("Speech started")
        elif event.type == "end":
            print(f"Speech ended, {len(event.audio)} samples to transcribe")

    # Between sessions (e.g. after each call):
    detector.reset()
"""

from __future__ import annotations

import collections
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np
import torch

from server.config import VADConfig

log = logging.getLogger(__name__)

# Silero VAD requires exactly this many samples per call at 16 kHz.
_SILERO_WINDOW_SAMPLES: int = 512  # 32 ms at 16 kHz
_SAMPLE_RATE: int = 16_000

# Pre-speech padding: keep this many milliseconds of audio before the VAD
# trigger so that the leading edge of words is not clipped.
_PRE_SPEECH_PAD_MS: int = 300
_PRE_SPEECH_PAD_SAMPLES: int = int(_SAMPLE_RATE * _PRE_SPEECH_PAD_MS / 1000)


class _State(Enum):
    IDLE = auto()
    SPEAKING = auto()


@dataclass
class SpeechEvent:
    """A state-transition event emitted by :class:`SpeechDetector`.

    Attributes
    ----------
    type:
        ``"start"`` when speech is first detected, or ``"end"`` when the
        utterance is considered complete (silence exceeded the configured
        ``silence_duration_ms``).
    audio:
        The full accumulated speech buffer (16 kHz mono float32 numpy array)
        returned only on ``type == "end"`` events.  ``None`` for ``"start"``
        events.
    """

    type: str
    audio: np.ndarray | None = field(default=None)


class SpeechDetector:
    """Streaming speech detector backed by the Silero VAD model.

    Detects speech start/end transitions in a 16 kHz mono float32 audio
    stream.  Emits :class:`SpeechEvent` objects on transitions; returns
    ``None`` when no transition has occurred.

    The detector is thread-safe: ``process_chunk`` may be called from the
    discord.py voice-receive thread while ``reset`` is called from the main
    event loop.

    Parameters
    ----------
    config:
        A :class:`~server.config.VADConfig` instance controlling the silence
        threshold and the minimum silence duration that ends an utterance.

    Notes
    -----
    - Silero VAD requires exactly 512 samples per call at 16 kHz.  Incoming
      chunks of any size are windowed internally.
    - A short ring-buffer (~300 ms) is maintained before speech begins so
      that the very start of words is never clipped.
    - The Silero model is loaded once on construction and runs on CPU.
    """

    def __init__(self, config: VADConfig) -> None:
        """Initialise the detector and load the Silero VAD model.

        Args:
            config: VAD configuration (threshold, silence_duration_ms).
        """
        self._config = config

        # Compute the number of 512-sample windows that correspond to the
        # configured silence duration.
        frames_per_second = _SAMPLE_RATE / _SILERO_WINDOW_SAMPLES  # 31.25
        silence_frames = int(
            (config.silence_duration_ms / 1000.0) * frames_per_second
        )
        # Always require at least one frame of silence.
        self._silence_frame_threshold: int = max(1, silence_frames)

        log.debug(
            "SpeechDetector: threshold=%.2f silence_duration_ms=%d "
            "silence_frame_threshold=%d pre_speech_pad_samples=%d",
            config.threshold,
            config.silence_duration_ms,
            self._silence_frame_threshold,
            _PRE_SPEECH_PAD_SAMPLES,
        )

        # --- Load Silero VAD ---
        log.info("Loading Silero VAD model…")
        self._model, self._utils = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            verbose=False,
        )
        self._model.eval()

        # VADIterator wraps the model with stateful streaming logic.
        # We drive it manually using the raw model probabilities so we can
        # apply our own threshold and silence-duration logic.
        # (VADIterator's built-in threshold / min_silence logic is less
        # flexible, so we keep the iterator only for its hidden RNN state.)
        log.info("Silero VAD model loaded successfully")

        # --- State ---
        self._lock = threading.Lock()
        self._state: _State = _State.IDLE

        # Ring-buffer holding the most recent _PRE_SPEECH_PAD_SAMPLES samples
        # so we can prepend them to the speech buffer on a "start" transition.
        self._pre_buffer: collections.deque[np.ndarray] = collections.deque()
        self._pre_buffer_samples: int = 0

        # Accumulated audio frames during SPEAKING state.
        self._speech_chunks: list[np.ndarray] = []

        # Carry-over from the previous process_chunk call: samples that did
        # not fill a complete 512-sample window yet.
        self._remainder: np.ndarray = np.empty(0, dtype=np.float32)

        # Number of consecutive silent 512-sample frames seen while SPEAKING.
        self._silence_frame_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_chunk(self, chunk: np.ndarray) -> SpeechEvent | None:
        """Feed a chunk of audio into the VAD and return any state-transition event.

        The chunk may be any size; it will be buffered internally until full
        512-sample Silero windows can be formed.

        Args:
            chunk: 16 kHz mono float32 numpy array of any length.

        Returns:
            A :class:`SpeechEvent` if the VAD transitioned state, or ``None``
            if the state has not changed.  Only one event is returned per
            call; if both a "start" and an "end" somehow occur within a single
            chunk (very short utterance) the "end" event is returned with the
            accumulated audio.
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        with self._lock:
            return self._process_locked(chunk)

    def reset(self) -> None:
        """Clear all internal state.

        Call this between sessions (e.g. when a call ends) to ensure that
        stale audio from one session does not bleed into the next.
        """
        with self._lock:
            self._state = _State.IDLE
            self._pre_buffer.clear()
            self._pre_buffer_samples = 0
            self._speech_chunks.clear()
            self._remainder = np.empty(0, dtype=np.float32)
            self._silence_frame_count = 0
            # Reset the Silero model's RNN hidden state.
            self._model.reset_states()
        log.debug("SpeechDetector.reset() called")

    # ------------------------------------------------------------------
    # Internal helpers (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _process_locked(self, chunk: np.ndarray) -> SpeechEvent | None:
        """Core processing logic (lock already held by caller)."""
        # Prepend any leftover samples from the previous call.
        if self._remainder.size > 0:
            chunk = np.concatenate([self._remainder, chunk])

        # Slice the chunk into 512-sample windows; save any trailing samples.
        num_windows = chunk.size // _SILERO_WINDOW_SAMPLES
        self._remainder = chunk[num_windows * _SILERO_WINDOW_SAMPLES :]

        last_event: SpeechEvent | None = None

        for i in range(num_windows):
            window = chunk[i * _SILERO_WINDOW_SAMPLES : (i + 1) * _SILERO_WINDOW_SAMPLES]
            event = self._process_window(window)
            if event is not None:
                last_event = event

        return last_event

    def _process_window(self, window: np.ndarray) -> SpeechEvent | None:
        """Run the Silero model on exactly one 512-sample window.

        Updates internal state and returns a :class:`SpeechEvent` on a
        IDLE -> SPEAKING or SPEAKING -> IDLE transition, or ``None``.
        """
        # Run Silero inference.
        tensor = torch.from_numpy(window).unsqueeze(0)  # shape: (1, 512)
        with torch.no_grad():
            prob: float = self._model(tensor, _SAMPLE_RATE).item()

        speech_detected: bool = prob >= self._config.threshold

        if self._state is _State.IDLE:
            # Always feed into the pre-speech ring-buffer so we have context.
            self._add_to_pre_buffer(window)

            if speech_detected:
                log.debug(
                    "VAD: IDLE -> SPEAKING (prob=%.3f threshold=%.2f)",
                    prob,
                    self._config.threshold,
                )
                self._state = _State.SPEAKING
                self._silence_frame_count = 0

                # Seed the speech buffer with pre-speech padding.
                pre = self._drain_pre_buffer()
                if pre is not None:
                    self._speech_chunks.append(pre)
                # Also include this window itself.
                self._speech_chunks.append(window.copy())

                return SpeechEvent(type="start")

        elif self._state is _State.SPEAKING:
            # Accumulate every window regardless of VAD result; we do not
            # want to punch holes in the audio we send to Whisper.
            self._speech_chunks.append(window.copy())

            if not speech_detected:
                self._silence_frame_count += 1
                if self._silence_frame_count >= self._silence_frame_threshold:
                    # Silence persisted long enough — end of utterance.
                    audio = self._collect_speech_buffer()
                    log.debug(
                        "VAD: SPEAKING -> IDLE (prob=%.3f silence_frames=%d "
                        "audio_samples=%d audio_duration_s=%.3f)",
                        prob,
                        self._silence_frame_count,
                        audio.size,
                        audio.size / _SAMPLE_RATE,
                    )
                    self._state = _State.IDLE
                    self._silence_frame_count = 0
                    return SpeechEvent(type="end", audio=audio)
            else:
                # Speech is still active — reset the silence counter.
                self._silence_frame_count = 0

        return None

    def _add_to_pre_buffer(self, window: np.ndarray) -> None:
        """Append *window* to the pre-speech ring-buffer, evicting old samples
        if the buffer would exceed ``_PRE_SPEECH_PAD_SAMPLES``."""
        self._pre_buffer.append(window.copy())
        self._pre_buffer_samples += window.size

        # Evict oldest windows until we are within the pad limit.
        while self._pre_buffer_samples - _PRE_SPEECH_PAD_SAMPLES > _SILERO_WINDOW_SAMPLES:
            evicted = self._pre_buffer.popleft()
            self._pre_buffer_samples -= evicted.size

    def _drain_pre_buffer(self) -> np.ndarray | None:
        """Return all pre-speech padding as a single contiguous array and
        clear the buffer.  Returns ``None`` if the buffer is empty."""
        if not self._pre_buffer:
            return None
        audio = np.concatenate(list(self._pre_buffer), axis=0)
        self._pre_buffer.clear()
        self._pre_buffer_samples = 0
        return audio

    def _collect_speech_buffer(self) -> np.ndarray:
        """Concatenate all accumulated speech chunks into a single array and
        reset the speech buffer."""
        audio = np.concatenate(self._speech_chunks, axis=0) if self._speech_chunks else np.empty(0, dtype=np.float32)
        self._speech_chunks.clear()
        return audio
