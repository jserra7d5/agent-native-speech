# Implementation Plan: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-voice-qol-features/spec.md`

## Summary

Add 10 quality-of-life features to the Discord voice agent MCP server: stop-token speech mode, mode toggling, auto-detect voice channel, migration from stdio to Streamable HTTP transport for multi-client support, slash-command spawning of coding agent CLIs (Claude Code, Codex) in visible terminal windows, per-session TTS voice assignment, an LLM-powered voice switchboard for routing messages between concurrent agent sessions, session management, session browsing/resume, and a first-time `init` CLI wizard for guided setup and daemon installation.

The existing codebase is a Python MCP server using discord.py with Silero VAD + Faster-Whisper STT + Qwen3-TTS/ElevenLabs TTS. The major architectural changes are: (1) migrating the MCP transport from stdio to Streamable HTTP via `mcp.server.streamable_http_manager.StreamableHTTPSessionManager` + Starlette/uvicorn, (2) introducing a session registry and voice switchboard that replaces the single-call model with a multi-session message-routing model, (3) adding a spawn subsystem that launches CLI processes in terminal windows and wires them to the HTTP MCP endpoint, and (4) a CLI init wizard for one-time setup.

## Technical Context

**Language/Version**: Python 3.14 (from .venv)
**Primary Dependencies**: discord.py[voice] >=2.5, discord-ext-voice-recv >=0.5.1, mcp >=1.0, faster-whisper >=1.1.0, torch >=2.0, qwen-tts >=0.1, anthropic >=0.40, starlette (for HTTP transport), uvicorn (ASGI server), thefuzz (fuzzy session name matching)
**Storage**: File-based (corrections JSON, session logs JSONL, config TOML/env). No database.
**Testing**: pytest + pytest-asyncio
**Target Platform**: Linux desktop (systemd for daemon, x-terminal-emulator for spawn)
**Project Type**: MCP server + Discord bot + CLI tool (init command)
**Performance Goals**: Router LLM intent classification <500ms, TTS callback <30s after spawn, switchboard announcement transition <3s
**Constraints**: Single GPU shared between TTS and STT models (mutual exclusion loading), localhost-only HTTP binding, single Discord voice channel active at a time
**Scale/Scope**: Single user, 5+ concurrent MCP client sessions, 10+ voice pool voices

## Constitution Check

*No constitution file found. Gate skipped.*

## Project Structure

### Documentation (this feature)

```text
specs/001-voice-qol-features/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: entity definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/           # Phase 1: interface contracts
│   ├── mcp-tools.md     # MCP tool schemas (new + modified)
│   ├── discord-commands.md  # Discord slash command contracts
│   └── cli-commands.md  # Host CLI commands (init)
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
server/
├── __init__.py
├── main.py              # Entry point — add HTTP transport, dual-mode startup
├── http_app.py          # NEW: Starlette ASGI app for Streamable HTTP transport
├── config.py            # Extend with new config fields (speech mode, spawn, router, init)
├── call_manager.py      # Refactor → session_manager.py (multi-session, switchboard)
├── session_manager.py   # NEW: replaces call_manager, multi-session orchestration
├── switchboard.py       # NEW: voice switchboard (queue, routing, announcements)
├── router.py            # NEW: LLM intent router (classify voice commands)
├── spawn.py             # NEW: terminal spawn, process tracking, CLI detection
├── session_browser.py   # NEW: read Claude Code/Codex session metadata
├── voice_pool.py        # NEW: curated voice pool assignment
├── speech_mode.py       # NEW: pause/stop-token mode toggling + stop-word detection
├── discord_bot.py       # Extend with new slash commands (/spawn, /sessions, etc.)
├── stt_pipeline.py      # Extend listen() for stop-token mode
├── tts_backend.py       # No changes (protocol stable)
├── tts_engine.py        # No changes
├── elevenlabs_tts.py    # No changes
├── audio_source.py      # No changes
├── audio_sink.py        # No changes
├── vad.py               # No changes
├── voice_profile.py     # No changes (voice_pool.py wraps this)
├── transcriber.py       # No changes
├── correction.py        # No changes
├── check_messages.py    # NEW: MCP tool for cold call message retrieval
├── hooks/               # NEW: hook scripts for Claude Code PostToolUse
│   └── check_voice_queue.sh  # PostToolUse hook that checks message queue
└── init/                # NEW: init CLI wizard
    ├── __init__.py
    ├── wizard.py         # Interactive setup flow
    ├── systemd.py        # Systemd user service creation
    └── mcp_register.py   # MCP server registration in CLI configs

tests/
├── unit/
│   ├── test_speech_mode.py
│   ├── test_switchboard.py
│   ├── test_router.py
│   ├── test_voice_pool.py
│   ├── test_spawn.py
│   ├── test_session_browser.py
│   └── test_init.py
└── integration/
    ├── test_http_transport.py
    └── test_multi_session.py
```

**Structure Decision**: Extend the existing `server/` package with new modules. No separate packages — this is a single-project structure. New modules are added alongside existing ones to keep imports simple. The `init/` subpackage groups the setup wizard logic. The `hooks/` directory contains shell scripts for Claude Code hook integration (not Python modules).

## Complexity Tracking

> No constitution violations to justify.
