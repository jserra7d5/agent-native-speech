# agent-native-speech

MCP server that lets AI agents call users on Discord voice channels. Supports multi-client HTTP transport for concurrent agent sessions.

## Architecture

- **MCP server** (`server/main.py`) -- dual-transport (HTTP default, stdio with HTTP sidecar)
- **HTTP app** (`server/http_app.py`) -- Starlette ASGI with `StreamableHTTPSessionManager`
- **Discord bot** (`server/discord_bot.py`) -- background thread via `BotRunner`
- **SessionManager** (`server/session_manager.py`) -- multi-session registry wrapping CallManager
- **CallManager** (`server/call_manager.py`) -- bridges MCP tools to Discord voice ops
- **TTS** -- pluggable via `TTSBackend` protocol: local Qwen3-TTS (`tts_engine.py`) or ElevenLabs (`elevenlabs_tts.py`) with voice pooling
- **STT** (`server/stt_pipeline.py`) -- Silero VAD + transcriber (local Whisper or ElevenLabs Scribe) + LLM correction
- **ElevenLabs STT** (`server/elevenlabs_stt.py`) -- cloud STT via ElevenLabs Scribe API
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
cp config.json.example config.json  # fill in discord_token, etc.
python -m server.main                          # HTTP transport (default)
python -m server.main --config config.json     # explicit config path
python -m server.main --transport stdio        # stdio + HTTP sidecar for spawned agents
python -m server.init                          # first-time setup wizard
```

See `config.json.example` for all settings. Legacy `.env` files still work (auto-detected). Key config: `discord_token`, `tts.backend` (local/elevenlabs), `tts.default_voice`, `stt.backend` (local/elevenlabs), `speech_mode.mode` (pause/stop_token), `server.transport` (http/stdio), `llm` (shared LLM backend for router + correction).

## MCP Tools

`initiate_call`, `continue_call`, `speak_to_user`, `end_call`, `check_messages`

## Discord Slash Commands

`/correct`, `/corrections`, `/speech`, `/spawn`, `/sessions`, `/kill`, `/resume`, `/voices`

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

`.env`, `config.json`, `voices/*/prompt_cache.pt`, `data/corrections/*.json`, `data/sessions/`
