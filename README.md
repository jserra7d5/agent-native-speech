# agent-native-speech

An MCP server that gives AI agents the ability to **call you on Discord** and have real voice conversations. The agent speaks via text-to-speech, listens via speech-to-text, and manages the full call lifecycle through MCP tools.

Built for [Claude Code](https://claude.com/claude-code) and any MCP-compatible AI client.

```
Claude Code (or any MCP client)
    │  initiate_call / continue_call / speak_to_user / end_call
    │  HTTP (Streamable HTTP) or stdio (MCP JSON-RPC)
    ▼
MCP Server (multi-client HTTP default, stdio fallback)
    ├── TTS: Qwen3-TTS local GPU  ─or─  ElevenLabs cloud API (with voice pooling)
    ├── STT: Silero VAD → Faster-Whisper ─or─ ElevenLabs Scribe → LLM correction
    ├── Voice Pool: per-session TTS voice assignment
    ├── Switchboard: multi-session message queuing/routing
    └── Discord Bot: discord.py + voice-recv (DAVE E2EE support)
            │
            ▼
    Discord Voice Channel
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `initiate_call(channel_id, message)` | Join a voice channel, speak, listen, return transcript |
| `continue_call(call_id, message)` | Speak a follow-up and listen for reply |
| `speak_to_user(call_id, message)` | One-way TTS announcement (no listen) |
| `end_call(call_id, message)` | Speak farewell and disconnect |
| `add_correction(wrong, right)` | Teach an STT word correction |
| `list_corrections()` | List all stored corrections |
| `set_speech_mode(mode)` | Switch between pause and stop_token listening modes |
| `list_sessions()` | List all active agent sessions |
| `check_messages()` | Check for queued voice messages from the user |

## Discord Slash Commands

| Command | Description |
|---------|-------------|
| `/correct` | Add an STT word correction |
| `/corrections` | List all stored corrections |
| `/mode` | Switch speech completion mode (pause/stop_token) |
| `/stopword` | Set the stop word for stop_token mode |
| `/spawn` | Launch a new Claude/Codex instance in a target directory |
| `/sessions` | List all active agent sessions |
| `/kill` | Terminate a session |
| `/resume` | Resume a previous session |

## Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA (for local TTS/STT) — or use ElevenLabs cloud for both
- A [Discord bot](https://discord.com/developers/applications) with voice permissions
- An [Anthropic API key](https://console.anthropic.com/) or an OpenAI-compatible LLM (for STT correction)

## Quick Start

```bash
git clone https://github.com/jserra7d5/agent-native-voice.git
cd agent-native-voice
python -m venv .venv
source .venv/bin/activate
pip install -e '.[tts]'

# Option A: JSON config (recommended)
cp config.json.example config.json
# Edit config.json: discord_token, elevenlabs_api_key, etc.

# Option B: Legacy .env (still supported)
cp .env.example .env
# Edit .env: DISCORD_TOKEN, ANTHROPIC_API_KEY, etc.
```

Run:

```bash
source .venv/bin/activate
python -m server.main                          # HTTP transport (default)
python -m server.main --config config.json     # Explicit config path
python -m server.main --transport stdio        # stdio (legacy single-client)
```

Or use the interactive setup wizard:

```bash
python -m server.init
```

Or add as an MCP server in Claude Code:

```bash
claude mcp add voice-agent python -m server.main
```

## Configuration

The server uses `config.json` (preferred) or legacy `.env` files. Config is auto-detected with this precedence:

1. Explicit `--config` path
2. Local `./config.json`
3. Local `./.env` (legacy)
4. Global `~/.config/voice-agent/config.json`
5. Global `~/.config/voice-agent/config.env` (legacy)

See [`config.json.example`](config.json.example) for the full annotated schema. The setup wizard (`python -m server.init`) generates a config interactively.

### Key Configuration Sections

```jsonc
{
  "discord_token": "...",           // Required
  "anthropic_api_key": "...",       // For Anthropic SDK correction (optional)
  "elevenlabs_api_key": "...",      // Shared by TTS and STT when using ElevenLabs

  "tts": {
    "backend": "elevenlabs",        // "local" (Qwen3-TTS) or "elevenlabs"
    "default_voice": "Ryan",
    "elevenlabs": {
      "default_voice_id": "CYDz...",
      "voices": {                   // Voice alias map for pooling
        "Ryan": "CYDzJWiIyIiQuhRB4r1K",
        "Aiden": "pNInz6obpgDQGcFmaJgB",
        "system": "21m00Tcm4TlvDq8ikWAM"
      }
    }
  },

  "stt": {
    "backend": "local",             // "local" (Whisper) or "elevenlabs" (Scribe)
    "model": "medium",
    "elevenlabs": {
      "model_id": "scribe_v2",
      "language_code": "eng"
    }
  },

  "llm": {                          // Shared LLM backend for router + correction
    "backend": "openrouter",
    "model": "meta-llama/llama-4-scout",
    "api_key": "..."
  },

  "voice_pool": ["Ryan", "Aiden"],  // Per-session voice assignment
  "system_voice": "system"          // Reserved for switchboard announcements
}
```

## Text-to-Speech Backends

Select a backend via `tts.backend` in `config.json` (or `TTS_BACKEND` in `.env`).

### Local: Qwen3-TTS (default)

Uses [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) on your GPU. No API costs, full privacy. Supports both preset voices and custom voice cloning.

```json
{
  "tts": {
    "backend": "local",
    "default_voice": "Ryan",
    "device": "cuda"
  }
}
```

**Preset voices** (no setup needed): `Ryan`, `Aiden` (English), `Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric` (Chinese), `Ono_Anna` (Japanese), `Sohee` (Korean).

### Cloud: ElevenLabs

Uses the [ElevenLabs API](https://elevenlabs.io/) for high-quality cloud TTS. No GPU needed for speech — frees VRAM for a larger Whisper model. Supports **voice pooling** for multi-session differentiation.

```json
{
  "elevenlabs_api_key": "your-api-key",
  "tts": {
    "backend": "elevenlabs",
    "default_voice": "Ryan",
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
  "voice_pool": ["Ryan", "Aiden"],
  "system_voice": "system"
}
```

The `voices` map assigns friendly names to ElevenLabs voice IDs. The `voice_pool` list references these names — each concurrent agent session gets a distinct voice so the user can tell agents apart by sound.

## Speech-to-Text Backends

Select a backend via `stt.backend` in `config.json` (or `STT_BACKEND` in `.env`).

### Local: Faster-Whisper (default)

Uses [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) with [Silero VAD](https://github.com/snakers4/silero-vad) for voice activity detection.

```json
{
  "stt": {
    "backend": "local",
    "model": "medium",
    "device": "cuda",
    "compute_type": "float16"
  }
}
```

### Cloud: ElevenLabs Scribe

Uses [ElevenLabs Scribe v2](https://elevenlabs.io/docs/api-reference/speech-to-text) for cloud-based transcription. No GPU needed for STT. VAD still runs locally for speech boundary detection.

```json
{
  "elevenlabs_api_key": "your-api-key",
  "stt": {
    "backend": "elevenlabs",
    "elevenlabs": {
      "model_id": "scribe_v2",
      "language_code": "eng"
    }
  }
}
```

Set `language_code` to an ISO-639 code (e.g. `"eng"`, `"fra"`, `"deu"`) or omit for auto-detection.

### STT Corrections

When the STT engine consistently mishears a word, teach it via MCP tool or Discord slash command:

```
/correct "Klode" "Claude"
/correct "eye dent" "ident"
```

Corrections are stored per user (`data/corrections/{user_id}.json`) and applied via LLM for context-aware substitution. The LLM backend is configurable — uses Anthropic SDK if `anthropic_api_key` is set, otherwise falls back to the shared `llm` config (OpenRouter, OpenAI-compatible, etc.).

## Speech Modes

Two modes for detecting when the user has finished speaking:

- **Pause mode** (default): Silence-based turn detection. The bot waits for a configurable silence duration before processing speech.
- **Stop token mode**: The user says a keyword (default: "over") to signal they're done speaking. Allows longer, uninterrupted speech.

Switch modes via `set_speech_mode` MCP tool or `/mode` Discord command.

```json
{
  "speech_mode": {
    "mode": "pause",
    "stop_word": "over",
    "max_timeout_s": 60.0
  }
}
```

## Multi-Session Support

The server supports multiple concurrent agent sessions over HTTP transport. Each session gets:

- A **unique voice** from the voice pool so the user can tell agents apart
- A **message queue** via the switchboard for asynchronous messaging
- **Session tracking** with lifecycle management

Spawn new sessions via the `/spawn` Discord command or programmatically. The `voice_pool` config controls which voices are available for assignment.

## LLM Backend Configuration

The `llm` config section provides a shared backend for both the intent router and STT correction. Supports multiple backends:

```json
{
  "llm": {
    "backend": "openrouter",
    "model": "meta-llama/llama-4-scout",
    "api_key": "sk-or-...",
    "timeout_ms": 2000
  }
}
```

| Backend | Description |
|---------|-------------|
| `openrouter` | [OpenRouter](https://openrouter.ai/) API |
| `codex_oauth` | OpenAI API with Codex OAuth credentials |
| `openai_compatible` | Any OpenAI-compatible endpoint |

The router and correction can override `model` and `timeout_ms` individually. If `anthropic_api_key` is set, STT correction uses the Anthropic SDK directly (backward compatible).

## Custom Voice Cloning (Local)

Clone any voice from a short reference audio clip using the Qwen3-TTS Base model. Create character voices, impressions, or clone your own voice.

### What You Need

- **3-30 seconds** of clean reference audio (WAV, mono preferred)
- An accurate **transcript** of what's spoken in the audio
- Minimal background noise, music, or reverb

### Step by Step

**1. Create a voice profile directory:**

```bash
mkdir -p voices/my_voice
```

**2. Add reference audio** as `reference.wav`. For best results, use 10-30 seconds of a single speaker with consistent tone. Concatenate multiple clips if needed:

```bash
ffmpeg -i clip1.wav -i clip2.wav -i clip3.wav \
  -filter_complex "[0][1][2]concat=n=3:v=0:a=1" \
  voices/my_voice/reference.wav
```

**3. Create `voices/my_voice/profile.json`:**

```json
{
  "name": "my_voice",
  "display_name": "My Custom Voice",
  "type": "clone",
  "language": "English",
  "ref_audio": "reference.wav",
  "ref_text": "The exact words spoken in the reference audio, transcribed accurately.",
  "x_vector_only": false
}
```

| Field | Description |
|-------|-------------|
| `name` | Must match directory name |
| `type` | Must be `"clone"` |
| `language` | `"English"`, `"Chinese"`, `"Japanese"`, or `"Korean"` |
| `ref_audio` | Reference WAV filename (relative to profile directory) |
| `ref_text` | Exact transcript of the reference audio |
| `x_vector_only` | `false` = best quality, `true` = faster but lower quality |

**4. Set `tts.default_voice` to `"my_voice"` in `config.json` and start the server.**

On first use, the model extracts a voice clone prompt and caches it to `prompt_cache.pt`. Subsequent startups skip extraction.

### Tips for Better Clones

- **One speaker per profile** — don't mix voices in reference audio
- **Accurate transcripts matter** — the model uses them to align speech features
- **Iterate** — try different reference clips if the clone sounds off
- **Temperature tuning** — edit `CLONE_GENERATE_KWARGS` in `server/tts_engine.py` to adjust creativity vs. stability (default `0.3` for consistency)

### Example: 343 Guilty Spark

The repo includes a sample profile at `voices/guilty_spark/profile.json` for 343 Guilty Spark from Halo CE. Add your own `reference.wav` with dialogue and set `tts.default_voice` to `"guilty_spark"`.

## VRAM Requirements

| Component | VRAM |
|-----------|------|
| Whisper `base` | ~1 GB |
| Whisper `medium` | ~5 GB |
| Whisper `large-v3` | ~10 GB |
| Qwen3-TTS (one model) | ~4-5 GB |
| **Typical (medium + TTS)** | **~10 GB** |

Using ElevenLabs for TTS and/or STT eliminates local GPU requirements for those components, allowing you to run on lighter hardware or use a larger Whisper model.

## Architecture

```
server/
├── main.py              # MCP server entry point (HTTP default, stdio fallback)
├── config.py            # JSON + .env configuration loading
├── http_app.py          # Starlette ASGI app for Streamable HTTP transport
├── session_manager.py   # Multi-session registry wrapping CallManager
├── call_manager.py      # Session lifecycle, bridges MCP ↔ Discord
├── discord_bot.py       # Discord bot + voice channel management
├── tts_backend.py       # TTSBackend protocol + shared text preprocessing
├── tts_engine.py        # Local Qwen3-TTS (preset + voice cloning)
├── elevenlabs_tts.py    # ElevenLabs cloud TTS with voice pooling
├── voice_profile.py     # Voice profile registry (presets + clone profiles)
├── voice_pool.py        # Per-session TTS voice assignment
├── stt_pipeline.py      # Orchestrates: AudioSink → VAD → transcriber → correction
├── transcriber.py       # Faster-Whisper transcription (local)
├── elevenlabs_stt.py    # ElevenLabs Scribe transcription (cloud)
├── audio_sink.py        # Receives Discord audio, resamples 48kHz→16kHz
├── audio_source.py      # Converts TTS audio → Discord PCM playback
├── vad.py               # Silero VAD streaming speech detection
├── correction.py        # Per-user correction dictionaries + LLM correction
├── speech_mode.py       # Pause and stop_token speech completion modes
├── switchboard.py       # Multi-session message queuing/routing
├── router.py            # LLM intent classification for multi-session routing
├── spawn.py             # Terminal detection + CLI agent process spawning
├── session_browser.py   # Browse previous Claude/Codex sessions
├── check_messages.py    # MCP tool for checking pending voice messages
└── init/                # Setup wizard, MCP registration, systemd service
```

**Threading model**: MCP server runs in the main asyncio thread. Discord bot runs in a background daemon thread with its own event loop. `BotRunner.run_coroutine()` bridges them.

**Audio pipeline**:
- **Receive**: Discord 48kHz stereo → resample 16kHz mono → Silero VAD → Whisper/Scribe → LLM correction
- **Playback**: TTS 24kHz mono float32 → resample 48kHz stereo int16 → Discord voice. Multi-sentence messages use streaming playback with voice pooling for multi-session differentiation.

**Transport**: HTTP (Streamable HTTP via Starlette/uvicorn) is the default for multi-client support. Legacy stdio transport is available for single-client MCP setups.

## License

MIT
