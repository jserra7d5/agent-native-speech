# Configuration Reference

## Config Loading Precedence

`Config.load(config_path)` searches in this order (first match wins):

1. **Explicit path** (`--config` CLI flag) -- detected as JSON by `.json` suffix, otherwise treated as .env
2. **Local `./config.json`** -- standard project-local config
3. **Local `./.env`** -- legacy format, logged as info
4. **Global `~/.config/voice-agent/config.json`** -- for daemon/global installs
5. **Global `~/.config/voice-agent/config.env`** -- legacy global, logged as warning with migration suggestion

If no config is found, returns defaults (which will fail validation due to missing `discord_token`).

## config.json Full Schema

```json
{
  "discord_token": "YOUR_DISCORD_BOT_TOKEN",
  "anthropic_api_key": "",
  "elevenlabs_api_key": "",
  "default_channel_id": null,
  "preload_models": false,

  "stt": {
    "backend": "local",
    "model": "medium",
    "device": "cuda",
    "compute_type": "float16",
    "elevenlabs": {
      "model_id": "scribe_v2",
      "language_code": "eng"
    }
  },

  "tts": {
    "backend": "elevenlabs",
    "default_voice": "Ryan",
    "device": "cuda",
    "voices_dir": "voices",
    "elevenlabs": {
      "model_id": "eleven_flash_v2_5",
      "default_voice_id": "CYDzJWiIyIiQuhRB4r1K",
      "voices": {
        "Ryan": "CYDzJWiIyIiQuhRB4r1K",
        "Aiden": "pNInz6obpgDQGcFmaJgB",
        "system": "21m00Tcm4TlvDq8ikWAM"
      }
    }
  },

  "vad": {
    "silence_duration_ms": 1500,
    "threshold": 0.5
  },

  "correction": {
    "model": ""
  },

  "speech_mode": {
    "mode": "pause",
    "stop_word": "over",
    "max_timeout_s": 60.0,
    "stop_confirm_ms": 1500,
    "clear_token": "clear",
    "chime_enabled": true,
    "chime_frequency_hz": 880,
    "chime_duration_ms": 150
  },

  "spawn": {
    "default_cli": "claude",
    "terminal_override": ""
  },

  "llm": {
    "backend": "openrouter",
    "model": "meta-llama/llama-4-scout",
    "api_key": "",
    "api_base_url": "",
    "codex_auth_path": "~/.codex/auth.json",
    "timeout_ms": 2000
  },

  "router": {
    "enabled": false,
    "model": "",
    "timeout_ms": 500
  },

  "server": {
    "host": "127.0.0.1",
    "port": 8765,
    "transport": "http"
  },

  "voice_pool": ["Ryan", "Aiden"],
  "system_voice": "system",
  "max_queue_depth": 20
}
```

## Dataclass Hierarchy

All config classes are in `server/config.py` and use `@dataclass` with default values.

### Top-level: `Config`

| Field | Type | Default | Notes |
|---|---|---|---|
| `discord_token` | `str` | `""` | **Required**. Bot token from Discord developer portal. |
| `anthropic_api_key` | `str` | `""` | For LLM correction (legacy path). |
| `elevenlabs_api_key` | `str` | `""` | Shared by ElevenLabs TTS and STT backends. |
| `default_channel_id` | `int \| None` | `None` | Auto-join this channel. None = auto-detect user's channel. |
| `preload_models` | `bool` | `False` | If true, warmup STT + TTS models at startup. |
| `voice_pool` | `list[str]` | `[]` | Voice names for per-session assignment (e.g. `["Ryan", "Aiden"]`). |
| `system_voice` | `str` | `""` | Voice for system announcements (not assigned from pool). |
| `max_queue_depth` | `int` | `20` | Max queued messages per session in Switchboard. |

### `STTConfig` (nested as `stt`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `backend` | `str` | `"local"` | `stt.backend` | `"local"` (Whisper) or `"elevenlabs"` (Scribe v2) |
| `model` | `str` | `"base"` | `stt.model` | Whisper model size: tiny, base, small, medium, large-v3 |
| `device` | `str` | `"cuda"` | `stt.device` | torch device |
| `compute_type` | `str` | `"float16"` | `stt.compute_type` | faster-whisper compute type |
| `elevenlabs_model_id` | `str` | `"scribe_v2"` | `stt.elevenlabs.model_id` | ElevenLabs STT model |
| `elevenlabs_language_code` | `str` | `"eng"` | `stt.elevenlabs.language_code` | ISO language code |

### `TTSConfig` (nested as `tts`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `backend` | `str` | `"local"` | `tts.backend` | `"local"` (Qwen3-TTS) or `"elevenlabs"` (cloud) |
| `default_voice` | `str` | `"Ryan"` | `tts.default_voice` | Default voice profile name |
| `device` | `str` | `"cuda"` | `tts.device` | torch device for local TTS |
| `voices_dir` | `str` | `"voices"` | `tts.voices_dir` | Directory for voice clone profiles |
| `elevenlabs_voice_id` | `str` | `""` | `tts.elevenlabs.default_voice_id` | Default ElevenLabs voice ID |
| `elevenlabs_model_id` | `str` | `"eleven_flash_v2_5"` | `tts.elevenlabs.model_id` | ElevenLabs TTS model |
| `elevenlabs_voices` | `dict[str, str]` | `{}` | `tts.elevenlabs.voices` | Name-to-voice-ID alias map |

### `VADConfig` (nested as `vad`)

| Field | Type | Default | JSON path |
|---|---|---|---|
| `silence_duration_ms` | `int` | `1500` | `vad.silence_duration_ms` |
| `threshold` | `float` | `0.5` | `vad.threshold` |

### `CorrectionConfig` (nested as `correction`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `model` | `str` | `""` | `correction.model` | Override model. Empty = use `llm.model` or legacy Anthropic. |
| `data_dir` | `Path` | `data/corrections` | (not in JSON) | Directory for correction JSON files. |

### `SpeechModeConfig` (nested as `speech_mode`)

| Field | Type | Default | JSON path |
|---|---|---|---|
| `mode` | `str` | `"pause"` | `speech_mode.mode` |
| `stop_word` | `str` | `"over"` | `speech_mode.stop_word` |
| `max_timeout_s` | `float` | `60.0` | `speech_mode.max_timeout_s` |
| `stop_confirm_ms` | `int` | `1500` | `speech_mode.stop_confirm_ms` |
| `clear_token` | `str` | `"clear"` | `speech_mode.clear_token` |
| `chime_enabled` | `bool` | `True` | `speech_mode.chime_enabled` |
| `chime_frequency_hz` | `int` | `880` | `speech_mode.chime_frequency_hz` |
| `chime_duration_ms` | `int` | `150` | `speech_mode.chime_duration_ms` |

### `SpawnConfig` (nested as `spawn`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `default_cli` | `str` | `"claude"` | `spawn.default_cli` | `"claude"` or `"codex"` |
| `terminal_override` | `str` | `""` | `spawn.terminal_override` | Force specific terminal emulator |
| `server_url` | `str` | `"http://127.0.0.1:8765/mcp"` | `spawn.server_url` | Auto-derived from server host/port if not set |

### `LLMConfig` (nested as `llm`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `backend` | `str` | `""` | `llm.backend` | `"openrouter"`, `"codex_oauth"`, `"openai_compatible"` |
| `model` | `str` | `""` | `llm.model` | Model identifier for the backend |
| `api_key` | `str` | `""` | `llm.api_key` | API key for the backend |
| `api_base_url` | `str` | `""` | `llm.api_base_url` | Custom base URL for OpenAI-compatible |
| `codex_auth_path` | `str` | `~/.codex/auth.json` | `llm.codex_auth_path` | Path to Codex OAuth credentials |
| `timeout_ms` | `int` | `2000` | `llm.timeout_ms` | Request timeout in milliseconds |

### `RouterConfig` (nested as `router`)

| Field | Type | Default | JSON path | Notes |
|---|---|---|---|---|
| `enabled` | `bool` | `False` | `router.enabled` | Enable LLM intent routing for multi-session |
| `model` | `str` | `""` | `router.model` | Override model (empty = use `llm.model`) |
| `timeout_ms` | `int` | `0` | `router.timeout_ms` | Override timeout (0 = use `llm.timeout_ms`) |

### `ServerConfig` (nested as `server`)

| Field | Type | Default | JSON path |
|---|---|---|---|
| `host` | `str` | `"127.0.0.1"` | `server.host` |
| `port` | `int` | `8765` | `server.port` |
| `transport` | `str` | `"http"` | `server.transport` |

## Legacy .env Variable Mapping

The `Config.from_env()` method reads these environment variables:

| Env var | Maps to | Default |
|---|---|---|
| `DISCORD_TOKEN` | `discord_token` | `""` |
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | `""` |
| `ELEVENLABS_API_KEY` | `elevenlabs_api_key` | `""` |
| `DISCORD_CHANNEL_ID` | `default_channel_id` | `None` |
| `PRELOAD_MODELS` | `preload_models` | `false` |
| `STT_BACKEND` | `stt.backend` | `"local"` |
| `WHISPER_MODEL` | `stt.model` | `"base"` |
| `WHISPER_DEVICE` | `stt.device` | `"cuda"` |
| `WHISPER_COMPUTE_TYPE` | `stt.compute_type` | `"float16"` |
| `TTS_BACKEND` | `tts.backend` | `"local"` |
| `TTS_VOICE` | `tts.default_voice` | `"Ryan"` |
| `TTS_DEVICE` | `tts.device` | `"cuda"` |
| `TTS_VOICES_DIR` | `tts.voices_dir` | `"voices"` |
| `ELEVENLABS_VOICE_ID` | `tts.elevenlabs_voice_id` | `""` |
| `ELEVENLABS_MODEL_ID` | `tts.elevenlabs_model_id` | `"eleven_flash_v2_5"` |
| `SILENCE_DURATION_MS` | `vad.silence_duration_ms` | `1500` |
| `VAD_THRESHOLD` | `vad.threshold` | `0.5` |
| `CORRECTION_MODEL` | `correction.model` | `""` |
| `SPEECH_MODE` | `speech_mode.mode` | `"pause"` |
| `STOP_WORD` | `speech_mode.stop_word` | `"over"` |
| `SPEECH_MAX_TIMEOUT_S` | `speech_mode.max_timeout_s` | `60.0` |
| `DEFAULT_CLI` | `spawn.default_cli` | `"claude"` |
| `TERMINAL_EMULATOR` | `spawn.terminal_override` | `""` |
| `SERVER_URL` | `spawn.server_url` | derived |
| `ROUTER_BACKEND` | `llm.backend` | `""` |
| `ROUTER_MODEL` | `llm.model` | `""` |
| `ROUTER_API_KEY` | `llm.api_key` | `""` |
| `ROUTER_API_BASE_URL` | `llm.api_base_url` | `""` |
| `ROUTER_CODEX_AUTH_PATH` | `llm.codex_auth_path` | `~/.codex/auth.json` |
| `ROUTER_TIMEOUT_MS` | `llm.timeout_ms` | `2000` |
| `ROUTER_ENABLED` | `router.enabled` | `false` |
| `SERVER_HOST` | `server.host` | `"127.0.0.1"` |
| `SERVER_PORT` | `server.port` | `8765` |
| `SERVER_TRANSPORT` | `server.transport` | `"http"` |
| `VOICE_POOL` | `voice_pool` | `[]` (comma-separated) |
| `SYSTEM_VOICE` | `system_voice` | `""` |
| `MAX_QUEUE_DEPTH` | `max_queue_depth` | `20` |

## Validation Rules

`Config.validate()` returns a list of error strings. The server exits if any errors exist.

| Rule | Error message |
|---|---|
| `discord_token` is empty | "DISCORD_TOKEN is required" |
| `tts.backend` not in (local, elevenlabs) | "TTS_BACKEND must be 'local' or 'elevenlabs'" |
| elevenlabs TTS without API key | "elevenlabs_api_key is required when tts.backend=elevenlabs" |
| elevenlabs TTS without voice ID or voices map | "Either tts.elevenlabs.default_voice_id or tts.elevenlabs.voices is required" |
| `stt.backend` not in (local, elevenlabs) | "stt.backend must be 'local' or 'elevenlabs'" |
| elevenlabs STT without API key | "elevenlabs_api_key is required when stt.backend=elevenlabs" |
| `speech_mode.mode` not in (pause, stop_token) | "SPEECH_MODE must be 'pause' or 'stop_token'" |
| `spawn.default_cli` not in (claude, codex) | "DEFAULT_CLI must be 'claude' or 'codex'" |
| `server.transport` not in (http, stdio) | "SERVER_TRANSPORT must be 'http' or 'stdio'" |
| `router.enabled` without `llm.backend` | "llm.backend is required when router.enabled=true" |

## JSON Parsing Notes

The `Config._from_dict()` method handles the nesting translation. Key mappings that differ between JSON structure and dataclass field names:

- `tts.elevenlabs.default_voice_id` in JSON maps to `TTSConfig.elevenlabs_voice_id`
- `tts.elevenlabs.voices` in JSON maps to `TTSConfig.elevenlabs_voices`
- `tts.elevenlabs.model_id` in JSON maps to `TTSConfig.elevenlabs_model_id`
- `stt.elevenlabs.model_id` in JSON maps to `STTConfig.elevenlabs_model_id`
- `stt.elevenlabs.language_code` in JSON maps to `STTConfig.elevenlabs_language_code`
- `spawn.server_url` auto-derives from `server.host` and `server.port` if not explicitly set
