# agent-native-speech

MCP server that lets AI agents call you on Discord voice channels. Built on the [CallMe](https://github.com/ZeframLou/call-me) pattern — same tool interface, but using Discord voice instead of phone calls and local GPU inference instead of cloud APIs.

## How it works

```
Claude Code (or any MCP client)
    │  initiate_call / continue_call / speak_to_user / end_call
    │  stdio (MCP JSON-RPC)
    ▼
MCP Server
    ├── STT Pipeline: Silero VAD → Faster-Whisper → Claude Haiku correction
    ├── TTS Engine: Qwen3-TTS (sentence-streaming playback)
    └── Discord Bot: discord.py + voice-recv (DAVE E2EE support)
            │
            ▼
    Discord Voice Channel
```

The agent calls `initiate_call` to join a voice channel and speak an opening message. The bot speaks via TTS, listens for the user's reply via STT, and returns the corrected transcript. The agent continues the conversation with `continue_call` and ends with `end_call`.

## MCP Tools

| Tool | Description |
|------|-------------|
| `initiate_call(channel_id, message)` | Join a voice channel, speak, listen, return transcript |
| `continue_call(call_id, message)` | Speak a follow-up and listen for reply |
| `speak_to_user(call_id, message)` | One-way TTS announcement (no listen) |
| `end_call(call_id, message)` | Speak farewell and disconnect |
| `add_correction(wrong, right)` | Teach an STT word correction |
| `list_corrections()` | List all stored corrections |

## Setup

### Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA (recommended: 16GB+ VRAM for Whisper medium + Qwen3-TTS)
- Discord bot with voice permissions
- Anthropic API key (for STT correction)

### Installation

```bash
git clone <repo-url>
cd agent-native-speech
python -m venv .venv
source .venv/bin/activate

# Core dependencies (STT + Discord)
pip install -e .

# TTS dependencies (Qwen3-TTS + FlashAttention)
pip install -e '.[tts]'
```

### Configuration

Copy `.env.example` and fill in your credentials:

```bash
cp .env.example .env
```

Required:
- `DISCORD_TOKEN` — Discord bot token
- `ANTHROPIC_API_KEY` — Anthropic API key

Optional:
| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_CHANNEL_ID` | — | Default voice channel (overridable per call) |
| `WHISPER_MODEL` | `base` | STT model: tiny/base/small/medium/large-v3 |
| `WHISPER_DEVICE` | `cuda` | Whisper compute device |
| `WHISPER_COMPUTE_TYPE` | `float16` | Whisper precision |
| `TTS_VOICE` | `Ryan` | TTS speaker (Ryan, Aiden, Vivian, etc.) |
| `TTS_DEVICE` | `cuda` | TTS compute device |
| `SILENCE_DURATION_MS` | `1500` | Silence to end an utterance |
| `VAD_THRESHOLD` | `0.5` | Voice detection sensitivity (0.0–1.0) |
| `CORRECTION_MODEL` | `claude-haiku-4-5-20251001` | LLM for transcript correction |
| `PRELOAD_MODELS` | `false` | Pre-load models at startup for lower first-call latency |

### Running

```bash
source .venv/bin/activate
python -m server.main
```

The server communicates over stdio using MCP JSON-RPC. All logging goes to stderr.

### Claude Code Integration

Add as an MCP server:

```bash
claude mcp add voice-agent python -m server.main
```

Or install the plugin from the project directory (uses `.claude-plugin/plugin.json`).

## Architecture

```
server/
├── main.py              # MCP server entry point (stdio transport)
├── config.py            # Environment-based configuration
├── discord_bot.py       # Discord bot + voice channel management
├── call_manager.py      # Session lifecycle, bridges MCP ↔ Discord
├── stt_pipeline.py      # Orchestrates: AudioSink → VAD → Whisper → correction
├── audio_sink.py        # Receives Discord audio, resamples 48kHz→16kHz
├── vad.py               # Silero VAD streaming speech detection
├── transcriber.py       # Faster-Whisper transcription with vocab biasing
├── correction.py        # Per-user correction dictionaries + Claude Haiku
├── tts_engine.py        # Qwen3-TTS synthesis with sentence chunking
└── audio_source.py      # Converts TTS audio → Discord PCM playback
```

**Threading model**: MCP server runs in the main asyncio thread (owns stdio). Discord bot runs in a background daemon thread with its own event loop. `BotRunner.run_coroutine()` bridges them via `asyncio.run_coroutine_threadsafe`.

**Audio pipeline**:
- **Receive**: Discord 48kHz stereo PCM → resample to 16kHz mono float32 → Silero VAD (512-sample windows) → Faster-Whisper → Claude Haiku correction
- **Playback**: Qwen3-TTS 24kHz mono float32 → resample to 48kHz stereo int16 → Discord voice. Multi-sentence messages use streaming playback (first sentence plays while rest synthesizes).

## Discord Slash Commands

The bot also registers Discord slash commands:

- `/correct wrong right` — Add an STT correction for your user
- `/corrections` — List your stored corrections

## STT Correction System

The correction system learns per-user vocabulary over time. When Whisper consistently mishears a word (names, technical terms, etc.), add a correction:

```
/correct "Klode" "Claude"
/correct "eye dent" "ident"
```

Corrections are:
- Stored as JSON files per user (`data/corrections/{user_id}.json`)
- Applied via Claude Haiku for context-aware substitution
- Used to bias Whisper's `initial_prompt` for better first-pass recognition

## GPU Memory

| Component | Approximate VRAM |
|-----------|-----------------|
| Whisper tiny | ~1 GB |
| Whisper base | ~1 GB |
| Whisper small | ~2 GB |
| Whisper medium | ~5 GB |
| Whisper large-v3 | ~10 GB |
| Qwen3-TTS 1.7B | ~4–7 GB |
| Silero VAD | CPU only |

Recommended: Whisper medium + Qwen3-TTS on a 16GB GPU (~12 GB total).

## License

MIT
