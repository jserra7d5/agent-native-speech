# Developer Quickstart: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01

## Prerequisites

- Python 3.10+ (3.14 in project venv)
- NVIDIA GPU with CUDA (for local TTS/STT) — or ElevenLabs API key for cloud TTS
- Discord bot token with voice permissions
- Claude Code (`claude`) and/or Codex CLI (`codex`) installed
- Linux desktop with systemd (for daemon setup)

## Setup

```bash
# Clone and activate
cd agent-native-speech
python -m venv .venv && source .venv/bin/activate

# Install with new dependencies
pip install -e '.[tts,dev]'

# New dependencies added for this feature:
# - starlette (ASGI framework for HTTP transport)
# - uvicorn (ASGI server)
# - thefuzz + python-Levenshtein (fuzzy session name matching)
```

## First-Time Setup (init wizard)

```bash
voice-agent init
```

This walks through all configuration interactively. See [contracts/cli-commands.md](contracts/cli-commands.md) for the full wizard flow.

For non-interactive setup:
```bash
voice-agent init \
  --discord-token "YOUR_TOKEN" \
  --tts-backend local \
  --tts-voice Ryan \
  --whisper-model base \
  --non-interactive
```

## Running the Server

```bash
# HTTP mode (multi-client, recommended)
voice-agent serve --transport http

# Or directly:
source .venv/bin/activate && python -m server.main --transport http

# Stdio mode (single-client, legacy)
voice-agent serve --transport stdio

# Via systemd (if configured by init):
systemctl --user start voice-agent
journalctl --user -u voice-agent -f
```

## Key Architecture Changes

### Before (current)

```
Claude Code ──stdio──> MCP Server ──> Discord Bot ──> Voice Channel
                       (single client, lifecycle tied)
```

### After (this feature)

```
Claude Code ──┐
Codex CLI   ──┤──HTTP/SSE──> MCP Server ──> Discord Bot ──> Voice Channel
Agent N     ──┘              (multi-client, persistent daemon)
                             │
                             ├── Session Manager (tracks all sessions)
                             ├── Switchboard (routes messages)
                             ├── Voice Pool (assigns TTS voices)
                             └── Speech Mode (pause/stop-token)
```

## New Module Overview

| Module | Purpose |
|--------|---------|
| `server/http_app.py` | Starlette ASGI app for Streamable HTTP transport |
| `server/session_manager.py` | Multi-session lifecycle management (replaces call_manager) |
| `server/switchboard.py` | Message queue, routing, System Voice announcements |
| `server/router.py` | LLM intent classification for voice commands |
| `server/spawn.py` | Terminal detection, CLI process spawning, PID tracking |
| `server/session_browser.py` | Read Claude Code/Codex session metadata from filesystem |
| `server/voice_pool.py` | Curated voice assignment for concurrent sessions |
| `server/speech_mode.py` | Pause/stop-token mode state and stop-word detection |
| `server/check_messages.py` | MCP tool for cold call message retrieval |
| `server/init/wizard.py` | Interactive setup wizard |
| `server/init/systemd.py` | Systemd user service creation |
| `server/init/mcp_register.py` | MCP server registration in CLI configs |
| `server/hooks/check_voice_queue.sh` | PostToolUse hook for Claude Code |

## Development Workflow

### Testing individual features

```bash
source .venv/bin/activate

# Run all tests
pytest tests/

# Test specific module
pytest tests/unit/test_speech_mode.py -v
pytest tests/unit/test_switchboard.py -v

# Test HTTP transport integration
pytest tests/integration/test_http_transport.py -v
```

### Testing the spawn flow end-to-end

1. Start the server: `voice-agent serve --transport http`
2. Join a Discord voice channel
3. In Discord, run: `/spawn directory:/path/to/project`
4. Verify: terminal opens, agent calls you back, you can brief it verbally

### Testing the switchboard

1. Start the server
2. `/spawn /project-a` — agent A calls you
3. `/spawn /project-b` — agent B calls you
4. Both agents report back: System Voice announces queued messages between readouts
5. Reply with voice commands: "tell project-a to go ahead", "next", etc.

### Testing speech modes

1. Start a call
2. `/mode mode:stop_token` — switch to stop-token mode
3. Speak with natural pauses — system should NOT cut you off
4. Say "over" after a pause — system finalizes transcript
5. `/mode mode:pause` — switch back to pause mode

## Environment Variables

All new environment variables (in addition to existing ones):

| Variable | Default | Description |
|----------|---------|-------------|
| `SPEECH_MODE` | `pause` | Default speech completion mode |
| `STOP_WORD` | `over` | Stop word for stop-token mode |
| `DEFAULT_CLI` | `claude` | Default CLI for /spawn |
| `TERMINAL_EMULATOR` | auto-detect | Terminal emulator override |
| `SERVER_HOST` | `127.0.0.1` | HTTP server bind address |
| `SERVER_PORT` | `8765` | HTTP server bind port |
| `ROUTER_ENABLED` | `false` | Enable LLM router |
| `ROUTER_BACKEND` | — | Router backend: codex_oauth, openrouter, openai_compatible |
| `ROUTER_MODEL` | — | Router LLM model identifier |
| `ROUTER_API_KEY` | — | Router API key (for openrouter/openai_compatible) |
| `ROUTER_API_BASE_URL` | — | Router base URL (for openai_compatible) |
| `MAX_QUEUE_DEPTH` | `20` | Maximum messages per session queue |
| `VOICE_POOL` | — | Comma-separated list of voice names for the curated pool |
| `SYSTEM_VOICE` | — | Voice name for switchboard announcements |
