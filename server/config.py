"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class STTConfig:
    model: str = "base"
    device: str = "cuda"
    compute_type: str = "float16"


@dataclass
class TTSConfig:
    voice: str = "Ryan"
    device: str = "cuda"


@dataclass
class VADConfig:
    silence_duration_ms: int = 1500
    threshold: float = 0.5


@dataclass
class CorrectionConfig:
    model: str = "claude-haiku-4-5-20251001"
    data_dir: Path = field(default_factory=lambda: Path("data/corrections"))


@dataclass
class Config:
    discord_token: str = ""
    anthropic_api_key: str = ""
    default_channel_id: int | None = None
    preload_models: bool = False

    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    correction: CorrectionConfig = field(default_factory=CorrectionConfig)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Config:
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        channel_id = os.getenv("DISCORD_CHANNEL_ID")

        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            default_channel_id=int(channel_id) if channel_id else None,
            preload_models=os.getenv("PRELOAD_MODELS", "false").lower() in ("true", "1", "yes"),
            stt=STTConfig(
                model=os.getenv("WHISPER_MODEL", "base"),
                device=os.getenv("WHISPER_DEVICE", "cuda"),
                compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
            ),
            tts=TTSConfig(
                voice=os.getenv("TTS_VOICE", "Ryan"),
                device=os.getenv("TTS_DEVICE", "cuda"),
            ),
            vad=VADConfig(
                silence_duration_ms=int(os.getenv("SILENCE_DURATION_MS", "1500")),
                threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
            ),
            correction=CorrectionConfig(
                model=os.getenv("CORRECTION_MODEL", "claude-haiku-4-5-20251001"),
            ),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.discord_token:
            errors.append("DISCORD_TOKEN is required")
        return errors
