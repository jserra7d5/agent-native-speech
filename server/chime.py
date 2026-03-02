"""Programmatic audio chime generation for STT feedback.

Generates short sine-wave chimes played through Discord voice to signal
recording state changes.  Arrays are cached at module level so each
unique parameter combination is only computed once.
"""

from __future__ import annotations

import numpy as np

# Module-level cache: (function_name, freq, duration, sr) -> ndarray
_cache: dict[tuple[str, int, int, int], np.ndarray] = {}


def generate_chime(
    frequency_hz: int = 880,
    duration_ms: int = 150,
    sample_rate: int = 48000,
) -> np.ndarray:
    """Generate a single-tone chime indicating recording is done.

    Produces a sine wave with a short fade-in and fade-out envelope to
    avoid audible clicks.  Played in both pause and stop_token modes
    when STT recording finishes.

    Args:
        frequency_hz: Tone frequency in Hz.
        duration_ms: Total duration in milliseconds.
        sample_rate: Output sample rate in Hz.

    Returns:
        Float32 mono numpy array in the range [-1, 1].
    """
    key = ("chime", frequency_hz, duration_ms, sample_rate)
    if key in _cache:
        return _cache[key]

    num_samples = int(sample_rate * duration_ms / 1000)
    t = np.arange(num_samples, dtype=np.float32) / sample_rate

    # Pure sine wave
    tone = np.sin(2 * np.pi * frequency_hz * t, dtype=np.float32)

    # Envelope: 10ms fade-in, 20ms fade-out
    _apply_envelope(tone, sample_rate, fade_in_ms=10, fade_out_ms=20)

    _cache[key] = tone
    return tone


def generate_clear_chime(
    frequency_hz: int = 880,
    duration_ms: int = 150,
    sample_rate: int = 48000,
) -> np.ndarray:
    """Generate a two-tone descending chime for transcript clear events.

    Sounds distinctly different from the regular chime by using two quick
    descending tones (high then low).  Played when the "clear" token
    resets the transcript in stop_token mode.

    Args:
        frequency_hz: Starting frequency in Hz (second tone is 0.75x).
        duration_ms: Total duration in milliseconds (each tone is half).
        sample_rate: Output sample rate in Hz.

    Returns:
        Float32 mono numpy array in the range [-1, 1].
    """
    key = ("clear_chime", frequency_hz, duration_ms, sample_rate)
    if key in _cache:
        return _cache[key]

    half_duration_ms = duration_ms // 2
    half_samples = int(sample_rate * half_duration_ms / 1000)
    t = np.arange(half_samples, dtype=np.float32) / sample_rate

    # First tone: high frequency
    tone_high = np.sin(2 * np.pi * frequency_hz * t, dtype=np.float32)
    _apply_envelope(tone_high, sample_rate, fade_in_ms=10, fade_out_ms=20)

    # Second tone: lower frequency (descending)
    freq_low = frequency_hz * 0.75
    tone_low = np.sin(2 * np.pi * freq_low * t, dtype=np.float32)
    _apply_envelope(tone_low, sample_rate, fade_in_ms=10, fade_out_ms=20)

    result = np.concatenate([tone_high, tone_low])

    _cache[key] = result
    return result


def _apply_envelope(
    audio: np.ndarray,
    sample_rate: int,
    fade_in_ms: int,
    fade_out_ms: int,
) -> None:
    """Apply fade-in and fade-out envelope to *audio* in place.

    Args:
        audio: 1-D float32 array to modify.
        sample_rate: Sample rate in Hz.
        fade_in_ms: Fade-in duration in milliseconds.
        fade_out_ms: Fade-out duration in milliseconds.
    """
    fade_in_samples = int(sample_rate * fade_in_ms / 1000)
    fade_out_samples = int(sample_rate * fade_out_ms / 1000)

    if fade_in_samples > 0:
        fade_in = np.linspace(0.0, 1.0, fade_in_samples, dtype=np.float32)
        audio[:fade_in_samples] *= fade_in

    if fade_out_samples > 0:
        fade_out = np.linspace(1.0, 0.0, fade_out_samples, dtype=np.float32)
        audio[-fade_out_samples:] *= fade_out
