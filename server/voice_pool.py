"""Per-session voice assignment from a curated voice pool.

Each concurrent agent session gets a distinct TTS voice so the user can
tell agents apart by sound.  When only one session is active, the system
default voice is used without pool assignment.

Assignment logic (from spec):
  1. Single session  -> default voice (no pool assignment)
  2. Explicit voice requested and available -> assign it
  3. Explicit voice unavailable -> next unassigned pool voice
  4. All pool voices assigned -> reuse least-recently-assigned with warning
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from server.config import Config

log = logging.getLogger(__name__)

# Sensible English-language defaults when the user hasn't configured a pool.
_DEFAULT_POOL_VOICES = ["Ryan", "Aiden", "Vivian", "Serena", "Dylan", "Eric"]


class VoicePool:
    """Manages TTS voice assignment for concurrent agent sessions.

    Parameters
    ----------
    pool_voices:
        Ordered list of voice profile names available for assignment.
        Falls back to ``_DEFAULT_POOL_VOICES`` if empty.
    default_voice:
        The voice used when only a single session is active (no pool
        assignment needed).  Also used as the ultimate fallback.
    system_voice:
        Voice reserved for switchboard/system announcements.  Never
        assigned to an agent session.
    """

    def __init__(
        self,
        pool_voices: Sequence[str] | None = None,
        default_voice: str = "Ryan",
        system_voice: str = "",
    ) -> None:
        self._default_voice = default_voice
        self._system_voice = system_voice

        # Build the ordered pool, excluding the system voice
        raw = list(pool_voices) if pool_voices else list(_DEFAULT_POOL_VOICES)
        self._pool: list[str] = [v for v in raw if v != system_voice]
        if not self._pool:
            self._pool = list(_DEFAULT_POOL_VOICES)

        # session_id -> assigned voice name
        self._assignments: dict[str, str] = {}

    @classmethod
    def from_config(cls, config: Config) -> VoicePool:
        """Create a VoicePool from the application Config."""
        return cls(
            pool_voices=config.voice_pool or None,
            default_voice=config.tts.default_voice,
            system_voice=config.system_voice,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def default_voice(self) -> str:
        """The voice used for single-session mode."""
        return self._default_voice

    @property
    def pool_voices(self) -> list[str]:
        """The ordered list of voices available in the pool."""
        return list(self._pool)

    @property
    def assignments(self) -> dict[str, str]:
        """Current session_id -> voice_name mapping (read-only copy)."""
        return dict(self._assignments)

    @property
    def active_session_count(self) -> int:
        """Number of sessions with voice assignments."""
        return len(self._assignments)

    def get_system_voice(self) -> str:
        """Return the voice reserved for system/switchboard announcements.

        Falls back to the default voice if no system voice is configured.
        """
        return self._system_voice or self._default_voice

    def assign_voice(
        self,
        session_id: str,
        requested_voice: str | None = None,
    ) -> str:
        """Assign a TTS voice to *session_id* and return the voice name.

        Parameters
        ----------
        session_id:
            Unique identifier for the agent session.
        requested_voice:
            Optional explicit voice preference.  Honoured if available
            (not already assigned to another session and not the system
            voice).

        Returns
        -------
        str
            The voice name assigned to this session.
        """
        # Already assigned? Return existing.
        if session_id in self._assignments:
            return self._assignments[session_id]

        assigned_voices = set(self._assignments.values())

        # --- Explicit request ---
        if requested_voice:
            if requested_voice == self._system_voice:
                log.warning(
                    "Requested voice %r is reserved for system; falling back to pool",
                    requested_voice,
                )
            elif requested_voice not in assigned_voices:
                self._assignments[session_id] = requested_voice
                log.info("Assigned requested voice %r to session %s", requested_voice, session_id)
                return requested_voice
            else:
                log.warning(
                    "Requested voice %r already assigned; falling back to pool",
                    requested_voice,
                )

        # --- Next unassigned pool voice ---
        for voice in self._pool:
            if voice not in assigned_voices:
                self._assignments[session_id] = voice
                log.info("Assigned pool voice %r to session %s", voice, session_id)
                return voice

        # --- All pool voices exhausted: reuse the least common ---
        # Pick the first pool voice (stable ordering) that has the fewest
        # current assignments so we spread reuse evenly.
        usage_counts: dict[str, int] = {}
        for v in self._assignments.values():
            usage_counts[v] = usage_counts.get(v, 0) + 1

        best_voice = self._pool[0]
        best_count = usage_counts.get(best_voice, 0)
        for voice in self._pool[1:]:
            count = usage_counts.get(voice, 0)
            if count < best_count:
                best_voice = voice
                best_count = count

        log.warning(
            "All pool voices in use; reusing %r for session %s",
            best_voice,
            session_id,
        )
        self._assignments[session_id] = best_voice
        return best_voice

    def release_voice(self, session_id: str) -> str | None:
        """Release the voice assignment for *session_id*.

        Returns the voice name that was released, or ``None`` if the
        session had no assignment.
        """
        voice = self._assignments.pop(session_id, None)
        if voice:
            log.info("Released voice %r from session %s", voice, session_id)
        return voice

    def get_voice(self, session_id: str) -> str | None:
        """Return the voice currently assigned to *session_id*, or None."""
        return self._assignments.get(session_id)

    def resolve_voice(self, session_id: str) -> str:
        """Return the effective voice for *session_id*.

        If only one session is active, returns the default voice (per
        spec: "single session = default voice, no pool assignment").
        Otherwise returns the pool-assigned voice.
        """
        if len(self._assignments) <= 1:
            return self._default_voice
        return self._assignments.get(session_id, self._default_voice)
