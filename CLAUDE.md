# agent-native-speech

MCP server that lets AI agents call users on Discord voice channels. Supports multi-client HTTP transport for concurrent agent sessions.

## Architecture

- **MCP server** (`server/main.py`) -- dual-transport (HTTP default, stdio fallback)
- **HTTP app** (`server/http_app.py`) -- Starlette ASGI with `StreamableHTTPSessionManager`
- **Discord bot** (`server/discord_bot.py`) -- background thread via `BotRunner`
- **SessionManager** (`server/session_manager.py`) -- multi-session registry wrapping CallManager
- **CallManager** (`server/call_manager.py`) -- bridges MCP tools to Discord voice ops
- **TTS** -- pluggable via `TTSBackend` protocol: local Qwen3-TTS (`tts_engine.py`) or ElevenLabs (`elevenlabs_tts.py`)
- **STT** (`server/stt_pipeline.py`) -- Silero VAD + Faster-Whisper + LLM correction
- **Speech modes** (`server/speech_mode.py`) -- pause (silence) and stop_token modes
- **Voice pool** (`server/voice_pool.py`) -- per-session TTS voice assignment
- **Switchboard** (`server/switchboard.py`) -- multi-session message queuing/routing
- **Router** (`server/router.py`) -- LLM intent classification for multi-session
- **Spawn** (`server/spawn.py`) -- terminal detection + CLI agent process spawning
- **Session browser** (`server/session_browser.py`) -- browse previous Claude/Codex sessions
- **Init wizard** (`server/init/`) -- setup wizard, MCP registration, systemd service

## Setup & Running

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[tts]'
cp .env.example .env  # fill in DISCORD_TOKEN, etc.
python -m server.main                    # HTTP transport (default)
python -m server.main --transport stdio  # stdio (legacy single-client)
python -m server.init                    # first-time setup wizard
```

See `.env.example` for all environment variables. Key ones: `DISCORD_TOKEN`, `TTS_BACKEND` (local/elevenlabs), `TTS_VOICE`, `SPEECH_MODE` (pause/stop_token), `SERVER_TRANSPORT` (http/stdio), `DEFAULT_CLI` (claude/codex).

## MCP Tools

`initiate_call`, `continue_call`, `speak_to_user`, `end_call`, `add_correction`, `list_corrections`, `set_speech_mode`, `list_sessions`, `check_messages`

## Discord Slash Commands

`/correct`, `/corrections`, `/mode`, `/stopword`, `/spawn`, `/sessions`, `/kill`, `/resume`

## Conventions

- TTS backends output `(float32 mono ndarray, sample_rate)` -- audio_source handles conversion
- Text preprocessing (code block stripping, sentence splitting) in `tts_backend.py`
- Local TTS uses mutual-exclusion model loading to stay within VRAM budget
- Voice clone profiles: `voices/<name>/profile.json` + `reference.wav`
- Discord bot thread communicates with MCP async loop via `BotRunner.run_coroutine()`
- Never write to stdout -- owned by MCP stdio transport
- SessionManager wraps CallManager; CallManager handles voice ops

## Testing

```bash
python -c "import server.main"  # verify imports
curl http://127.0.0.1:8765/health  # health check (HTTP mode)
```

## Files to Never Commit

`.env`, `voices/*/prompt_cache.pt`, `data/corrections/*.json`, `data/sessions/`
