# agent-native-speech

MCP server that lets AI agents call users on Discord voice channels. Exposes tools for initiating calls, speaking (TTS), listening (STT), and managing voice sessions.

## Architecture

- **MCP stdio server** (`server/main.py`) — runs in main asyncio loop
- **Discord bot** (`server/discord_bot.py`) — runs in background thread via `BotRunner`
- **CallManager** (`server/call_manager.py`) — bridges MCP tools to Discord voice ops
- **TTS** — pluggable backends via `TTSBackend` protocol (`server/tts_backend.py`):
  - `TTSEngine` (`server/tts_engine.py`) — local Qwen3-TTS (CustomVoice presets + Base voice cloning)
  - `ElevenLabsTTSEngine` (`server/elevenlabs_tts.py`) — ElevenLabs cloud API
- **STT** (`server/stt_pipeline.py`) — Silero VAD + Faster-Whisper + LLM correction
- **Audio** — `audio_source.py` (TTS→Discord 48kHz PCM), `audio_sink.py` (Discord→STT)
- **Voice profiles** (`server/voice_profile.py`) — preset speakers + clone profiles from `voices/`

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[tts]'
cp .env.example .env  # fill in DISCORD_TOKEN, etc.
```

## Running

```bash
source .venv/bin/activate && python -m server.main
```

Configured as an MCP server in `.mcp.json`. All logging goes to stderr + `/tmp/voice-agent.log`.

## Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TTS_BACKEND` | `local` | `local` (Qwen3-TTS, GPU) or `elevenlabs` (cloud) |
| `TTS_VOICE` | `Ryan` | Voice profile name (preset or clone) |
| `WHISPER_MODEL` | `base` | Whisper model size (`tiny`/`base`/`small`/`medium`/`large-v3`) |
| `ELEVENLABS_API_KEY` | — | Required when `TTS_BACKEND=elevenlabs` |
| `ELEVENLABS_VOICE_ID` | — | ElevenLabs voice ID |

See `.env.example` for full list.

## Conventions

- All TTS backends output `(float32 mono ndarray, sample_rate)` — audio_source handles conversion
- Text preprocessing (code block stripping, sentence splitting) lives in `tts_backend.py`, shared by all backends
- Local TTS uses mutual-exclusion model loading (one model at a time) to stay within VRAM budget
- Voice clone profiles live in `voices/<name>/profile.json` + `reference.wav`
- Discord bot thread communicates with MCP async loop via `BotRunner.run_coroutine()`
- Never write to stdout — it's owned by MCP stdio transport

## Testing

```bash
# Verify all imports
python -c "import server.main"

# Test via MCP tools: initiate_call, continue_call, speak_to_user, end_call
```

## Files to Never Commit

- `.env` (secrets)
- `voices/*/prompt_cache.pt` (generated cache)
- `data/corrections/*.json` (user data)
- `data/sessions/` (session logs)
