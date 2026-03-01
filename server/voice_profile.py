"""Voice profile system for TTS engine speaker management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from server.config import TTSConfig

log = logging.getLogger(__name__)

#: Speakers available in the Qwen3-TTS CustomVoice model.
#: Each entry is (speaker_name, native_language).
AVAILABLE_SPEAKERS: dict[str, str] = {
    "Vivian": "Chinese",
    "Serena": "Chinese",
    "Uncle_Fu": "Chinese",
    "Dylan": "Chinese",
    "Eric": "Chinese",
    "Ryan": "English",
    "Aiden": "English",
    "Ono_Anna": "Japanese",
    "Sohee": "Korean",
}


@dataclass
class VoiceProfile:
    name: str
    display_name: str
    profile_type: str  # "preset" or "clone"
    language: str
    # Preset-specific
    speaker: str | None = None
    # Clone-specific
    ref_audio_path: Path | None = None
    ref_text: str | None = None
    x_vector_only: bool = False


class VoiceProfileRegistry:
    """Registry of available voice profiles (presets + clones)."""

    def __init__(self, config: TTSConfig) -> None:
        self._profiles: dict[str, VoiceProfile] = {}
        self._register_presets()
        self._scan_clones(Path(config.voices_dir))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> VoiceProfile:
        """Lookup a profile by name. Raises KeyError if not found."""
        return self._profiles[name]

    def list_profiles(self) -> list[VoiceProfile]:
        """Return all registered profiles."""
        return list(self._profiles.values())

    def __contains__(self, name: str) -> bool:
        return name in self._profiles

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_presets(self) -> None:
        for speaker, language in AVAILABLE_SPEAKERS.items():
            self._profiles[speaker] = VoiceProfile(
                name=speaker,
                display_name=speaker,
                profile_type="preset",
                language=language,
                speaker=speaker,
            )

    def _scan_clones(self, voices_dir: Path) -> None:
        if not voices_dir.is_dir():
            log.debug("Voices directory %s does not exist, skipping clone scan", voices_dir)
            return

        for profile_file in sorted(voices_dir.glob("*/profile.json")):
            try:
                self._load_clone(profile_file)
            except Exception:
                log.warning("Failed to load voice profile from %s", profile_file, exc_info=True)

    def _load_clone(self, profile_file: Path) -> None:
        profile_dir = profile_file.parent
        data = json.loads(profile_file.read_text(encoding="utf-8"))

        name: str = data["name"]
        display_name: str = data.get("display_name", name)
        language: str = data.get("language", "English")
        x_vector_only: bool = data.get("x_vector_only", False)
        ref_audio_rel: str = data.get("ref_audio", "")
        ref_text: str = data.get("ref_text", "")

        # Resolve reference audio to absolute path
        ref_audio_path = (profile_dir / ref_audio_rel).resolve() if ref_audio_rel else None

        # Validation
        if ref_audio_path and not ref_audio_path.exists():
            log.warning("Profile %s: ref_audio %s does not exist, skipping", name, ref_audio_path)
            return

        if not x_vector_only and not ref_text:
            log.warning("Profile %s: ref_text is empty (required when x_vector_only is false), skipping", name)
            return

        self._profiles[name] = VoiceProfile(
            name=name,
            display_name=display_name,
            profile_type="clone",
            language=language,
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
            x_vector_only=x_vector_only,
        )
        log.info("Loaded clone voice profile: %s (%s)", display_name, name)
