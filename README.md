# agent-native-speech

An MCP server that gives AI agents the ability to **call you on Discord** and have real voice conversations. The agent speaks via text-to-speech, listens via speech-to-text, and manages the full call lifecycle through MCP tools.

Built for [Claude Code](https://claude.com/claude-code) and any MCP-compatible AI client.

```
Claude Code (or any MCP client)
    │  initiate_call / continue_call / speak_to_user / end_call
    │  stdio (MCP JSON-RPC)
    ▼
MCP Server
    ├── TTS: Qwen3-TTS local GPU  ─or─  ElevenLabs cloud API
    ├── STT: Silero VAD → Faster-Whisper → Claude Haiku correction
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

## Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA (for local TTS/STT) — or use ElevenLabs cloud TTS
- A [Discord bot](https://discord.com/developers/applications) with voice permissions
- An [Anthropic API key](https://console.anthropic.com/) (for STT correction)

## Quick Start

```bash
git clone https://github.com/jserra7d5/agent-native-voice.git
cd agent-native-voice
python -m venv .venv
source .venv/bin/activate
pip install -e '.[tts]'

cp .env.example .env
# Edit .env: DISCORD_TOKEN, ANTHROPIC_API_KEY, DISCORD_CHANNEL_ID
```

Run:

```bash
source .venv/bin/activate
python -m server.main
```

Or add as an MCP server in Claude Code:

```bash
claude mcp add voice-agent python -m server.main
```

## Text-to-Speech Backends

Select a backend via `TTS_BACKEND` in `.env`.

### Local: Qwen3-TTS (default)

Uses [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) on your GPU. No API costs, full privacy. Supports both preset voices and custom voice cloning.

```bash
TTS_BACKEND=local
TTS_VOICE=Ryan          # Preset or custom voice profile name
TTS_DEVICE=cuda
```

**Preset voices** (no setup needed): `Ryan`, `Aiden` (English), `Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric` (Chinese), `Ono_Anna` (Japanese), `Sohee` (Korean).

### Cloud: ElevenLabs

Uses the [ElevenLabs API](https://elevenlabs.io/) for high-quality cloud TTS. No GPU needed for speech — frees VRAM for a larger Whisper model.

```bash
TTS_BACKEND=elevenlabs
ELEVENLABS_API_KEY=your-api-key
ELEVENLABS_VOICE_ID=your-voice-id        # From ElevenLabs dashboard
ELEVENLABS_MODEL_ID=eleven_flash_v2_5    # eleven_flash_v2_5 (fast) or eleven_v3 (quality)
```

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

**4. Set `TTS_VOICE=my_voice` in `.env` and start the server.**

On first use, the model extracts a voice clone prompt and caches it to `prompt_cache.pt`. Subsequent startups skip extraction.

### Tips for Better Clones

- **One speaker per profile** — don't mix voices in reference audio
- **Accurate transcripts matter** — the model uses them to align speech features
- **Iterate** — try different reference clips if the clone sounds off
- **Temperature tuning** — edit `CLONE_GENERATE_KWARGS` in `server/tts_engine.py` to adjust creativity vs. stability (default `0.3` for consistency)

### Example: 343 Guilty Spark

The repo includes a sample profile at `voices/guilty_spark/profile.json` for 343 Guilty Spark from Halo CE. Add your own `reference.wav` with dialogue and set `TTS_VOICE=guilty_spark`.

## Speech-to-Text

Uses [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) with [Silero VAD](https://github.com/snakers4/silero-vad) for voice activity detection. Transcripts are post-corrected by Claude Haiku to fix common mishearings.

```bash
WHISPER_MODEL=medium        # tiny, base, small, medium, large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

### STT Corrections

When Whisper consistently mishears a word, teach it via MCP tool or Discord slash command:

```
/correct "Klode" "Claude"
/correct "eye dent" "ident"
```

Corrections are stored per user (`data/corrections/{user_id}.json`) and applied via Claude Haiku for context-aware substitution.

## Configuration Reference

See [`.env.example`](.env.example) for the full list.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | Yes | — | Discord bot token |
| `ANTHROPIC_API_KEY` | Yes | — | For STT post-correction |
| `DISCORD_CHANNEL_ID` | No | — | Default voice channel |
| `TTS_BACKEND` | No | `local` | `local` or `elevenlabs` |
| `TTS_VOICE` | No | `Ryan` | Voice profile name |
| `WHISPER_MODEL` | No | `base` | Whisper model size |
| `SILENCE_DURATION_MS` | No | `1500` | Silence to end utterance (ms) |
| `VAD_THRESHOLD` | No | `0.5` | Voice detection sensitivity |
| `PRELOAD_MODELS` | No | `false` | Pre-load models at startup |

## VRAM Requirements

| Component | VRAM |
|-----------|------|
| Whisper `base` | ~1 GB |
| Whisper `medium` | ~5 GB |
| Whisper `large-v3` | ~10 GB |
| Qwen3-TTS (one model) | ~4-5 GB |
| **Typical (medium + TTS)** | **~10 GB** |

Using ElevenLabs eliminates TTS VRAM entirely, allowing a larger Whisper model on the same GPU.

## Architecture

```
server/
├── main.py              # MCP server entry point (stdio transport)
├── config.py            # Environment-based configuration
├── tts_backend.py       # TTSBackend protocol + shared text preprocessing
├── tts_engine.py        # Local Qwen3-TTS (preset + voice cloning)
├── elevenlabs_tts.py    # ElevenLabs cloud TTS backend
├── voice_profile.py     # Voice profile registry (presets + clone profiles)
├── call_manager.py      # Session lifecycle, bridges MCP ↔ Discord
├── discord_bot.py       # Discord bot + voice channel management
├── stt_pipeline.py      # Orchestrates: AudioSink → VAD → Whisper → correction
├── audio_sink.py        # Receives Discord audio, resamples 48kHz→16kHz
├── audio_source.py      # Converts TTS audio → Discord PCM playback
├── vad.py               # Silero VAD streaming speech detection
├── transcriber.py       # Faster-Whisper transcription
└── correction.py        # Per-user correction dictionaries + Claude Haiku
```

**Threading model**: MCP server runs in the main asyncio thread (owns stdio). Discord bot runs in a background daemon thread with its own event loop. `BotRunner.run_coroutine()` bridges them.

**Audio pipeline**:
- **Receive**: Discord 48kHz stereo → resample 16kHz mono → Silero VAD → Faster-Whisper → Haiku correction
- **Playback**: TTS 24kHz mono float32 → resample 48kHz stereo int16 → Discord voice. Multi-sentence messages use streaming playback.

## License

MIT
