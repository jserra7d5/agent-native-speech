"""Speech completion mode management.

Controls how the STT pipeline determines when a user has finished speaking:
  - "pause" mode (default): silence detection via VAD triggers end-of-utterance
  - "stop_token" mode: user must say a keyword (e.g. "over") to signal completion
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from server.config import SpeechModeConfig

log = logging.getLogger(__name__)

# Pattern to strip trailing punctuation when checking for stop words
_TRAILING_PUNCT = re.compile(r"[.,!?;:]+$")


@dataclass
class SpeechMode:
    """Current speech mode state."""

    mode: str  # "pause" or "stop_token"
    stop_word: str
    max_timeout_s: float


class SpeechModeManager:
    """Manages the active speech completion mode.

    Thread-safe for reads; mutations are expected to be infrequent
    (slash commands or MCP tool calls).
    """

    def __init__(self, config: SpeechModeConfig) -> None:
        self._mode = SpeechMode(
            mode=config.mode,
            stop_word=config.stop_word,
            max_timeout_s=config.max_timeout_s,
        )

    def get_mode(self) -> str:
        """Return the current mode name ("pause" or "stop_token")."""
        return self._mode.mode

    def set_mode(self, mode: str, stop_word: str | None = None) -> dict[str, str]:
        """Update the speech completion mode.

        Args:
            mode: "pause" or "stop_token".
            stop_word: Optional new stop word (only relevant for stop_token mode).

        Returns:
            Dict with the new mode and stop_word values.

        Raises:
            ValueError: If mode is not "pause" or "stop_token".
        """
        if mode not in ("pause", "stop_token"):
            raise ValueError(f"Invalid speech mode: {mode!r} (must be 'pause' or 'stop_token')")
        self._mode.mode = mode
        if stop_word is not None:
            self._mode.stop_word = stop_word
        log.info("Speech mode set to %r (stop_word=%r)", self._mode.mode, self._mode.stop_word)
        return {"mode": self._mode.mode, "stop_word": self._mode.stop_word}

    def is_stop_token(self) -> bool:
        """Return True if the current mode is stop_token."""
        return self._mode.mode == "stop_token"

    @property
    def max_timeout_s(self) -> float:
        """Maximum timeout for stop_token accumulation."""
        return self._mode.max_timeout_s

    @property
    def stop_word(self) -> str:
        """The current stop word."""
        return self._mode.stop_word

    def check_stop_word(self, transcript: str) -> tuple[bool, str]:
        """Check if transcript ends with the stop word.

        The check is case-insensitive and ignores trailing punctuation.

        Args:
            transcript: The transcribed text segment to check.

        Returns:
            Tuple of (found, cleaned_transcript):
              - found: True if the stop word was detected at the end
              - cleaned_transcript: transcript with the trailing stop word removed
                (if found), or the original transcript (if not found)
        """
        stripped = transcript.strip()
        if not stripped:
            return False, transcript

        # Remove trailing punctuation for comparison
        cleaned = _TRAILING_PUNCT.sub("", stripped).rstrip()
        stop = self._mode.stop_word.lower()

        # Check if the cleaned text ends with the stop word
        if cleaned.lower().endswith(stop):
            # Remove the stop word from the end
            prefix = cleaned[: -len(stop)].rstrip()
            return True, prefix

        return False, transcript
