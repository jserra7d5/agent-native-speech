"""Configuration loading from environment variables and JSON files."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Fallback config paths for daemon/global installs
_GLOBAL_CONFIG_DIR = Path.home() / ".config" / "voice-agent"
_GLOBAL_JSON_PATH = _GLOBAL_CONFIG_DIR / "config.json"
_GLOBAL_ENV_PATH = _GLOBAL_CONFIG_DIR / "config.env"


@dataclass
class STTConfig:
    backend: str = "local"  # "local" (Whisper) or "elevenlabs" (Scribe v2)
    model: str = "base"
    device: str = "cuda"
    compute_type: str = "float16"
    # ElevenLabs STT-specific
    elevenlabs_model_id: str = "scribe_v2"
    elevenlabs_language_code: str = "eng"


@dataclass
class TTSConfig:
    backend: str = "local"  # "local" (Qwen3-TTS) or "elevenlabs" (cloud)
    default_voice: str = "Ryan"
    device: str = "cuda"
    voices_dir: str = "voices"
    # ElevenLabs-specific (only used when backend="elevenlabs")
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_flash_v2_5"
    # Voice alias map: friendly name -> ElevenLabs voice ID
    elevenlabs_voices: dict[str, str] = field(default_factory=dict)


@dataclass
class VADConfig:
    silence_duration_ms: int = 1500
    threshold: float = 0.5


@dataclass
class CorrectionConfig:
    model: str = ""  # override model (empty = use llm.model, or legacy anthropic)
    data_dir: Path = field(default_factory=lambda: Path("data/corrections"))


@dataclass
class SpeechModeConfig:
    mode: str = "pause"  # "pause" or "stop_token"
    stop_word: str = "over"
    max_timeout_s: float = 60.0
    stop_confirm_ms: int = 1500
    clear_token: str = "clear"
    chime_enabled: bool = True
    chime_frequency_hz: int = 880
    chime_duration_ms: int = 150


@dataclass
class SpawnConfig:
    default_cli: str = "claude"  # "claude" or "codex"
    terminal_override: str = ""
    server_url: str = "http://127.0.0.1:8765/mcp"


@dataclass
class LLMConfig:
    """Shared backend config for all LLM calls (router + correction)."""
    backend: str = ""  # "openrouter", "codex_oauth", "openai_compatible"
    model: str = ""
    api_key: str = ""
    api_base_url: str = ""
    codex_auth_path: str = str(Path.home() / ".codex" / "auth.json")
    timeout_ms: int = 2000


@dataclass
class RouterConfig:
    enabled: bool = False
    model: str = ""  # override (empty = use llm.model)
    timeout_ms: int = 0  # override (0 = use llm.timeout_ms)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    transport: str = "http"  # "http" or "stdio"


@dataclass
class Config:
    discord_token: str = ""
    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""  # shared by TTS and STT
    default_channel_id: int | None = None
    preload_models: bool = False

    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    correction: CorrectionConfig = field(default_factory=CorrectionConfig)
    speech_mode: SpeechModeConfig = field(default_factory=SpeechModeConfig)
    spawn: SpawnConfig = field(default_factory=SpawnConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # Voice pool
    voice_pool: list[str] = field(default_factory=list)
    system_voice: str = ""

    # Switchboard
    max_queue_depth: int = 20

    # ------------------------------------------------------------------
    # JSON loading
    # ------------------------------------------------------------------

    @classmethod
    def from_json(cls, path: str | Path) -> Config:
        """Load config from a JSON file."""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> Config:
        """Construct Config from a nested dict (JSON structure)."""
        stt_data = data.get("stt", {})
        el_stt = stt_data.get("elevenlabs", {})
        stt = STTConfig(
            backend=stt_data.get("backend", "local"),
            model=stt_data.get("model", "base"),
            device=stt_data.get("device", "cuda"),
            compute_type=stt_data.get("compute_type", "float16"),
            elevenlabs_model_id=el_stt.get("model_id", "scribe_v2"),
            elevenlabs_language_code=el_stt.get("language_code", "eng"),
        )

        tts_data = data.get("tts", {})
        el_tts = tts_data.get("elevenlabs", {})
        tts = TTSConfig(
            backend=tts_data.get("backend", "local"),
            default_voice=tts_data.get("default_voice", "Ryan"),
            device=tts_data.get("device", "cuda"),
            voices_dir=tts_data.get("voices_dir", "voices"),
            elevenlabs_voice_id=el_tts.get("default_voice_id", ""),
            elevenlabs_model_id=el_tts.get("model_id", "eleven_flash_v2_5"),
            elevenlabs_voices=el_tts.get("voices", {}),
        )

        vad_data = data.get("vad", {})
        vad = VADConfig(
            silence_duration_ms=vad_data.get("silence_duration_ms", 1500),
            threshold=vad_data.get("threshold", 0.5),
        )

        corr_data = data.get("correction", {})
        correction = CorrectionConfig(
            model=corr_data.get("model", ""),
        )

        sm_data = data.get("speech_mode", {})
        speech_mode = SpeechModeConfig(
            mode=sm_data.get("mode", "pause"),
            stop_word=sm_data.get("stop_word", "over"),
            max_timeout_s=sm_data.get("max_timeout_s", 60.0),
            stop_confirm_ms=sm_data.get("stop_confirm_ms", 1500),
            clear_token=sm_data.get("clear_token", "clear"),
            chime_enabled=sm_data.get("chime_enabled", True),
            chime_frequency_hz=sm_data.get("chime_frequency_hz", 880),
            chime_duration_ms=sm_data.get("chime_duration_ms", 150),
        )

        spawn_data = data.get("spawn", {})
        server_data = data.get("server", {})
        host = server_data.get("host", "127.0.0.1")
        port = server_data.get("port", 8765)
        spawn = SpawnConfig(
            default_cli=spawn_data.get("default_cli", "claude"),
            terminal_override=spawn_data.get("terminal_override", ""),
            server_url=spawn_data.get(
                "server_url", f"http://{host}:{port}/mcp"
            ),
        )

        llm_data = data.get("llm", {})
        llm = LLMConfig(
            backend=llm_data.get("backend", ""),
            model=llm_data.get("model", ""),
            api_key=llm_data.get("api_key", ""),
            api_base_url=llm_data.get("api_base_url", ""),
            codex_auth_path=llm_data.get(
                "codex_auth_path", str(Path.home() / ".codex" / "auth.json")
            ),
            timeout_ms=llm_data.get("timeout_ms", 2000),
        )

        router_data = data.get("router", {})
        router = RouterConfig(
            enabled=router_data.get("enabled", False),
            model=router_data.get("model", ""),
            timeout_ms=router_data.get("timeout_ms", 0),
        )

        server = ServerConfig(
            host=host,
            port=port,
            transport=server_data.get("transport", "http"),
        )

        channel_id = data.get("default_channel_id")

        return cls(
            discord_token=data.get("discord_token", ""),
            anthropic_api_key=data.get("anthropic_api_key", ""),
            elevenlabs_api_key=data.get("elevenlabs_api_key", ""),
            default_channel_id=int(channel_id) if channel_id else None,
            preload_models=data.get("preload_models", False),
            stt=stt,
            tts=tts,
            vad=vad,
            correction=correction,
            speech_mode=speech_mode,
            spawn=spawn,
            llm=llm,
            router=router,
            server=server,
            voice_pool=data.get("voice_pool", []),
            system_voice=data.get("system_voice", ""),
            max_queue_depth=data.get("max_queue_depth", 20),
        )

    # ------------------------------------------------------------------
    # Unified loader with precedence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load config with precedence:

        1. Explicit ``config_path`` (JSON or .env)
        2. Local ``./config.json``
        3. Local ``./.env`` (legacy, info log)
        4. Global ``~/.config/voice-agent/config.json``
        5. Global ``~/.config/voice-agent/config.env`` (legacy, warning log)
        """
        # 1. Explicit path
        if config_path:
            p = Path(config_path)
            if ".json" in p.suffixes or p.name.endswith(".json"):
                log.info("Loading config from explicit JSON path: %s", p)
                return cls.from_json(p)
            else:
                log.info("Loading config from explicit env path: %s", p)
                return cls.from_env(env_file=p)

        # 2. Local config.json
        if Path("config.json").exists():
            log.info("Loading config from local config.json")
            return cls.from_json("config.json")

        # 3. Local .env (legacy)
        if Path(".env").exists():
            log.info("Loading config from local .env (legacy format)")
            return cls.from_env()

        # 4. Global config.json
        if _GLOBAL_JSON_PATH.exists():
            log.info("Loading config from global %s", _GLOBAL_JSON_PATH)
            return cls.from_json(_GLOBAL_JSON_PATH)

        # 5. Global config.env (legacy)
        if _GLOBAL_ENV_PATH.exists():
            log.warning(
                "Loading config from legacy global %s — consider migrating "
                "to config.json by running 'python -m server.init'",
                _GLOBAL_ENV_PATH,
            )
            return cls.from_env(env_file=_GLOBAL_ENV_PATH)

        # No config found — return defaults (will fail validation)
        log.warning("No config file found; using defaults")
        return cls()

    # ------------------------------------------------------------------
    # Legacy .env loading (backward compat)
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Config:
        if env_file:
            load_dotenv(env_file)
        elif Path(".env").exists():
            load_dotenv()
        elif _GLOBAL_ENV_PATH.exists():
            load_dotenv(_GLOBAL_ENV_PATH)

        channel_id = os.getenv("DISCORD_CHANNEL_ID")

        # Parse voice pool from comma-separated list
        pool_raw = os.getenv("VOICE_POOL", "")
        voice_pool = [v.strip() for v in pool_raw.split(",") if v.strip()] if pool_raw else []

        # Map legacy ROUTER_* env vars to LLMConfig
        llm = LLMConfig(
            backend=os.getenv("ROUTER_BACKEND", ""),
            model=os.getenv("ROUTER_MODEL", ""),
            api_key=os.getenv("ROUTER_API_KEY", ""),
            api_base_url=os.getenv("ROUTER_API_BASE_URL", ""),
            codex_auth_path=os.getenv(
                "ROUTER_CODEX_AUTH_PATH",
                str(Path.home() / ".codex" / "auth.json"),
            ),
            timeout_ms=int(os.getenv("ROUTER_TIMEOUT_MS", "2000")),
        )

        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")

        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            elevenlabs_api_key=elevenlabs_api_key,
            default_channel_id=int(channel_id) if channel_id else None,
            preload_models=os.getenv("PRELOAD_MODELS", "false").lower() in ("true", "1", "yes"),
            stt=STTConfig(
                backend=os.getenv("STT_BACKEND", "local"),
                model=os.getenv("WHISPER_MODEL", "base"),
                device=os.getenv("WHISPER_DEVICE", "cuda"),
                compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
            ),
            tts=TTSConfig(
                backend=os.getenv("TTS_BACKEND", "local"),
                default_voice=os.getenv("TTS_VOICE", "Ryan"),
                device=os.getenv("TTS_DEVICE", "cuda"),
                voices_dir=os.getenv("TTS_VOICES_DIR", "voices"),
                elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
                elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
            ),
            vad=VADConfig(
                silence_duration_ms=int(os.getenv("SILENCE_DURATION_MS", "1500")),
                threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
            ),
            correction=CorrectionConfig(
                model=os.getenv("CORRECTION_MODEL", ""),
            ),
            speech_mode=SpeechModeConfig(
                mode=os.getenv("SPEECH_MODE", "pause"),
                stop_word=os.getenv("STOP_WORD", "over"),
                max_timeout_s=float(os.getenv("SPEECH_MAX_TIMEOUT_S", "60.0")),
                stop_confirm_ms=int(os.getenv("STOP_CONFIRM_MS", "1500")),
                clear_token=os.getenv("CLEAR_TOKEN", "clear"),
                chime_enabled=os.getenv("CHIME_ENABLED", "true").lower() in ("true", "1", "yes"),
                chime_frequency_hz=int(os.getenv("CHIME_FREQUENCY_HZ", "880")),
                chime_duration_ms=int(os.getenv("CHIME_DURATION_MS", "150")),
            ),
            spawn=SpawnConfig(
                default_cli=os.getenv("DEFAULT_CLI", "claude"),
                terminal_override=os.getenv("TERMINAL_EMULATOR", ""),
                server_url=os.getenv(
                    "SERVER_URL",
                    f"http://{os.getenv('SERVER_HOST', '127.0.0.1')}:{os.getenv('SERVER_PORT', '8765')}/mcp",
                ),
            ),
            llm=llm,
            router=RouterConfig(
                enabled=os.getenv("ROUTER_ENABLED", "false").lower() in ("true", "1", "yes"),
                model=os.getenv("ROUTER_MODEL", ""),
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
        if self.tts.backend == "elevenlabs" and not self.elevenlabs_api_key:
            errors.append("elevenlabs_api_key is required when tts.backend=elevenlabs")
        if self.tts.backend == "elevenlabs" and not self.tts.elevenlabs_voice_id and not self.tts.elevenlabs_voices:
            errors.append(
                "Either tts.elevenlabs.default_voice_id or tts.elevenlabs.voices "
                "is required when tts.backend=elevenlabs"
            )
        if self.stt.backend not in ("local", "elevenlabs"):
            errors.append(
                f"stt.backend must be 'local' or 'elevenlabs', got '{self.stt.backend}'"
            )
        if self.stt.backend == "elevenlabs" and not self.elevenlabs_api_key:
            errors.append("elevenlabs_api_key is required when stt.backend=elevenlabs")
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
        if self.router.enabled and not self.llm.backend:
            errors.append("llm.backend is required when router.enabled=true")
        return errors
