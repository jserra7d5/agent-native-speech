---
name: ans-server-setup
description: Server bootstrap, configuration, CLI integration, and init wizard for agent-native-speech. Use this skill when working on server startup, config loading, MCP tool registration, HTTP/stdio transport, Discord bot lifecycle, agent spawning, or the setup wizard.
---

# Server Setup & Bootstrap

## Overview

This skill covers the MCP server entry point, configuration system, transport layer, Discord bot lifecycle, agent process spawning, and the first-time setup wizard. These files collectively handle everything from `python -m server.main` through to a fully running voice agent server with registered MCP tools.

## File Map

| File | Role | Lines |
|---|---|---|
| `server/main.py` | MCP server entry point. Registers tools, creates components, runs transport. | ~634 |
| `server/config.py` | Config loading from JSON + legacy .env. Nested dataclasses, validation. | ~413 |
| `server/http_app.py` | Starlette ASGI app with StreamableHTTPSessionManager. Health endpoint. | ~61 |
| `server/discord_bot.py` | Discord.py bot in background thread. Slash commands. BotRunner cross-thread bridge. | ~851 |
| `server/spawn.py` | Terminal detection + CLI agent process spawning (Claude Code / Codex). | ~275 |
| `server/init/__init__.py` | CLI entry point: `voice-agent init` / `voice-agent serve` subcommands. | ~124 |
| `server/init/wizard.py` | Interactive 10-step setup wizard. Writes `~/.config/voice-agent/config.json`. | ~460 |
| `server/init/mcp_register.py` | Registers voice-agent as MCP server in Claude Code + Codex configs. | ~166 |
| `server/init/systemd.py` | Systemd user service creation, enable, start, status check. | ~79 |

## Startup Sequence

```
main() -> _parse_args() -> run(transport, config_path)
  |
  +-> _load_and_validate_config()     # Config.load() with precedence chain
  |     1. Explicit --config path (JSON or .env)
  |     2. Local ./config.json
  |     3. Local ./.env (legacy)
  |     4. Global ~/.config/voice-agent/config.json
  |     5. Global ~/.config/voice-agent/config.env (legacy)
  |
  +-> _init_components(config)
  |     1. BotRunner(config).start()        # Discord bot in background thread
  |     2. STTPipeline(config)              # VAD + Whisper + LLM correction
  |     3. bot.set_correction_manager()     # Wire CorrectionManager into bot
  |     4. SpeechModeManager(config)        # Wire into bot for /mode command
  |     5. _create_tts_engine(config)       # Local Qwen3-TTS or ElevenLabs
  |     6. Optional: warmup STT + TTS      # If preload_models=true
  |     7. SessionManager(...)             # Wraps CallManager + VoicePool
  |     8. SpawnManager(config)            # Wire into bot for /spawn command
  |     9. Server("agent-native-speech")   # MCP server + _register_handlers()
  |
  +-> Signal handlers (SIGINT, SIGTERM) -> shutdown_event.set()
  |
  +-> Transport:
  |     HTTP: _run_http() -> uvicorn.Server(create_app(mcp_server))
  |     stdio: _run_stdio() -> mcp.server.stdio.stdio_server()
  |
  +-> Shutdown (finally block):
        tts_engine.unload() -> stt_pipeline.unload() -> bot_runner.shutdown()
```

## Transport Modes

**HTTP (default)** -- Multi-client support via Starlette + StreamableHTTPSessionManager:
- MCP endpoint: `http://{host}:{port}/mcp` (POST)
- Health check: `http://{host}:{port}/health` (GET)
- Default: `127.0.0.1:8765`
- Session manager handles multi-client tracking automatically

**stdio (legacy)** -- Single-client MCP stdio transport:
- Reads/writes JSON-RPC over stdin/stdout
- **Never write to stdout** -- all logging goes to stderr + `/tmp/voice-agent.log`
- Used when MCP client connects directly via process pipes

Transport resolution: CLI `--transport` flag > `config.server.transport` > default "http"

## Configuration Overview

Config is a `@dataclass` hierarchy. Top-level `Config` has nested sub-configs:

| Sub-config | Key settings |
|---|---|
| `ServerConfig` | host, port, transport (http/stdio) |
| `TTSConfig` | backend (local/elevenlabs), default_voice, device, elevenlabs voice map |
| `STTConfig` | backend (local/elevenlabs), model (whisper size), device |
| `SpeechModeConfig` | mode (pause/stop_token), stop_word, max_timeout_s |
| `SpawnConfig` | default_cli (claude/codex), terminal_override, server_url |
| `LLMConfig` | backend, model, api_key, api_base_url, timeout_ms |
| `RouterConfig` | enabled, model override, timeout_ms override |
| `VADConfig` | silence_duration_ms, threshold |
| `CorrectionConfig` | model override, data_dir |

Top-level fields: `discord_token`, `anthropic_api_key`, `elevenlabs_api_key`, `default_channel_id`, `preload_models`, `voice_pool`, `system_voice`, `max_queue_depth`.

See `references/configuration.md` for the full JSON schema and all ~37 settings.

## MCP Tools (9 total)

| Tool | Required args | Returns |
|---|---|---|
| `initiate_call` | message; channel_id optional | call_id + first transcript |
| `continue_call` | call_id, message | user transcript |
| `speak_to_user` | call_id, message | confirmation |
| `end_call` | call_id, message | call duration |
| `add_correction` | wrong, right | confirmation |
| `list_corrections` | (none) | corrections dict |
| `set_speech_mode` | mode; stop_word optional | mode + stop_word |
| `list_sessions` | (none) | active sessions list |
| `check_messages` | (none) | queued messages |

See `references/mcp-tools.md` for full signatures and dispatch logic.

## Discord Bot & BotRunner

The Discord bot runs in a **separate thread** with its own asyncio event loop. Cross-thread communication uses `BotRunner.run_coroutine()` which calls `asyncio.run_coroutine_threadsafe()` with a 30s timeout.

Key wiring pattern -- components are set on the bot after construction:
```python
bot_runner.bot.set_correction_manager(stt_pipeline.correction_manager)
bot_runner.bot.set_speech_mode_manager(speech_mode_manager)
bot_runner.bot.set_spawn_manager(spawn_manager)
bot_runner.bot.set_session_manager(session_manager)
```

Slash commands: `/correct`, `/corrections`, `/mode`, `/stopword`, `/spawn`, `/sessions`, `/kill`, `/resume`

DAVE E2EE patches are applied at module import time via monkey-patching `PacketRouter._do_run` and `AudioReader.callback`.

See `references/discord-bot.md` for full slash command details and BotRunner API.

## Agent Spawning

`SpawnManager` launches Claude Code or Codex CLI instances that connect back to the voice server:

1. **TerminalDetector** finds a terminal emulator: config override > `x-terminal-emulator` > `$TERMINAL` > PATH scan (ghostty, kitty, alacritty, wezterm, gnome-terminal, ...)
2. Builds CLI command with inline MCP config pointing to `http://{host}:{port}/mcp`
3. Spawns in terminal window (interactive) or `subprocess.Popen` with devnull (headless)
4. Injects callback prompt: agent immediately calls `initiate_call` on startup

The spawned agent's MCP config is passed inline:
- Claude: `claude --mcp-config '{"mcpServers":{"voice-agent":{"url":"..."}}}'`
- Codex: `codex --mcp-config voice-agent=URL`

## Init Wizard

Run via `python -m server.init` or the `voice-agent init` CLI. 10-step interactive wizard:

1. Discord bot token
2. TTS backend (local/elevenlabs)
3. Default voice
4. STT backend
5. Default CLI (claude/codex)
6. Speech mode (pause/stop_token)
7. Whisper model size
8. Terminal emulator
9. Server host & port
10. Review

Post-wizard: MCP registration in detected CLIs + optional systemd service install.

Supports `--non-interactive` mode with CLI flags for all settings.

See `references/init-wizard.md` for the full wizard flow and registration details.

## Key Conventions

- **Logging**: stderr + `/tmp/voice-agent.log`. Never stdout (MCP stdio transport).
- **Thread model**: MCP server on main thread asyncio loop. Discord bot on background thread with its own loop.
- **Config precedence**: explicit path > local config.json > local .env > global config.json > global config.env
- **Validation**: `Config.validate()` returns list of error strings. Server exits on any error.
- **Shutdown**: SIGINT/SIGTERM sets an asyncio Event. Cleanup: TTS unload, STT unload, bot shutdown.

## References

- `references/configuration.md` -- Full config.json schema, all settings, dataclass hierarchy, env var mapping
- `references/mcp-tools.md` -- All 9 MCP tool signatures, parameters, return values, dispatch logic
- `references/discord-bot.md` -- Bot lifecycle, all slash commands, BotRunner API, DAVE patches, voice state handling
- `references/init-wizard.md` -- Setup wizard flow, MCP registration targets, systemd service template
