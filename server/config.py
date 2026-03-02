"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Fallback config path for daemon/global installs
_GLOBAL_CONFIG_PATH = Path.home() / ".config" / "voice-agent" / "config.env"


@dataclass
class STTConfig:
    model: str = "base"
    device: str = "cuda"
    compute_type: str = "float16"


@dataclass
class TTSConfig:
    backend: str = "local"  # "local" (Qwen3-TTS) or "elevenlabs" (cloud)
    voice: str = "Ryan"
    device: str = "cuda"
    voices_dir: str = "voices"
    # ElevenLabs-specific (only used when backend="elevenlabs")
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_flash_v2_5"


@dataclass
class VADConfig:
    silence_duration_ms: int = 1500
    threshold: float = 0.5


@dataclass
class CorrectionConfig:
    model: str = "claude-haiku-4-5-20251001"
    data_dir: Path = field(default_factory=lambda: Path("data/corrections"))


@dataclass
class SpeechModeConfig:
    mode: str = "pause"  # "pause" or "stop_token"
    stop_word: str = "over"
    max_timeout_s: float = 60.0


@dataclass
class SpawnConfig:
    default_cli: str = "claude"  # "claude" or "codex"
    terminal_override: str = ""
    server_url: str = "http://127.0.0.1:8765/mcp"


@dataclass
class RouterConfig:
    enabled: bool = False
    backend: str = ""  # "codex_oauth", "openrouter", "openai_compatible"
    model: str = ""
    api_key: str = ""
    api_base_url: str = ""
    codex_auth_path: str = str(Path.home() / ".codex" / "auth.json")
    timeout_ms: int = 500


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "http"  # "http" or "stdio"


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
    speech_mode: SpeechModeConfig = field(default_factory=SpeechModeConfig)
    spawn: SpawnConfig = field(default_factory=SpawnConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # Voice pool
    voice_pool: list[str] = field(default_factory=list)
    system_voice: str = ""

    # Switchboard
    max_queue_depth: int = 20

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Config:
        if env_file:
            load_dotenv(env_file)
        elif Path(".env").exists():
            load_dotenv()
        elif _GLOBAL_CONFIG_PATH.exists():
            load_dotenv(_GLOBAL_CONFIG_PATH)

        channel_id = os.getenv("DISCORD_CHANNEL_ID")

        # Parse voice pool from comma-separated list
        pool_raw = os.getenv("VOICE_POOL", "")
        voice_pool = [v.strip() for v in pool_raw.split(",") if v.strip()] if pool_raw else []

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
                backend=os.getenv("TTS_BACKEND", "local"),
                voice=os.getenv("TTS_VOICE", "Ryan"),
                device=os.getenv("TTS_DEVICE", "cuda"),
                voices_dir=os.getenv("TTS_VOICES_DIR", "voices"),
                elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
                elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
                elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
            ),
            vad=VADConfig(
                silence_duration_ms=int(os.getenv("SILENCE_DURATION_MS", "1500")),
                threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
            ),
            correction=CorrectionConfig(
                model=os.getenv("CORRECTION_MODEL", "claude-haiku-4-5-20251001"),
            ),
            speech_mode=SpeechModeConfig(
                mode=os.getenv("SPEECH_MODE", "pause"),
                stop_word=os.getenv("STOP_WORD", "over"),
                max_timeout_s=float(os.getenv("SPEECH_MAX_TIMEOUT_S", "60.0")),
            ),
            spawn=SpawnConfig(
                default_cli=os.getenv("DEFAULT_CLI", "claude"),
                terminal_override=os.getenv("TERMINAL_EMULATOR", ""),
                server_url=os.getenv(
                    "SERVER_URL",
                    f"http://{os.getenv('SERVER_HOST', '127.0.0.1')}:{os.getenv('SERVER_PORT', '8765')}/mcp",
                ),
            ),
            router=RouterConfig(
                enabled=os.getenv("ROUTER_ENABLED", "false").lower() in ("true", "1", "yes"),
                backend=os.getenv("ROUTER_BACKEND", ""),
                model=os.getenv("ROUTER_MODEL", ""),
                api_key=os.getenv("ROUTER_API_KEY", ""),
                api_base_url=os.getenv("ROUTER_API_BASE_URL", ""),
                codex_auth_path=os.getenv(
                    "ROUTER_CODEX_AUTH_PATH",
                    str(Path.home() / ".codex" / "auth.json"),
                ),
                timeout_ms=int(os.getenv("ROUTER_TIMEOUT_MS", "500")),
            ),
            server=ServerConfig(
                host=os.getenv("SERVER_HOST", "127.0.0.1"),
                port=int(os.getenv("SERVER_PORT", "8765")),
                transport=os.getenv("SERVER_TRANSPORT", "http"),
            ),
            voice_pool=voice_pool,
            system_voice=os.getenv("SYSTEM_VOICE", ""),
            max_queue_depth=int(os.getenv("MAX_QUEUE_DEPTH", "20")),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.discord_token:
            errors.append("DISCORD_TOKEN is required")
        if self.tts.backend not in ("local", "elevenlabs"):
            errors.append(
                f"TTS_BACKEND must be 'local' or 'elevenlabs', got '{self.tts.backend}'"
            )
        if self.tts.backend == "elevenlabs" and not self.tts.elevenlabs_api_key:
            errors.append("ELEVENLABS_API_KEY is required when TTS_BACKEND=elevenlabs")
        if self.tts.backend == "elevenlabs" and not self.tts.elevenlabs_voice_id:
            errors.append("ELEVENLABS_VOICE_ID is required when TTS_BACKEND=elevenlabs")
        if self.speech_mode.mode not in ("pause", "stop_token"):
            errors.append(
                f"SPEECH_MODE must be 'pause' or 'stop_token', got '{self.speech_mode.mode}'"
            )
        if self.spawn.default_cli not in ("claude", "codex"):
            errors.append(
                f"DEFAULT_CLI must be 'claude' or 'codex', got '{self.spawn.default_cli}'"
            )
        if self.server.transport not in ("http", "stdio"):
            errors.append(
                f"SERVER_TRANSPORT must be 'http' or 'stdio', got '{self.server.transport}'"
            )
        if self.router.enabled and not self.router.backend:
            errors.append("ROUTER_BACKEND is required when ROUTER_ENABLED=true")
        return errors
