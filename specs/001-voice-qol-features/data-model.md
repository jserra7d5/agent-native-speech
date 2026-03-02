# Data Model: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01

## Entities

### SpeechMode

Controls how the system determines when a user's speech turn is complete.

| Field | Type | Description |
|-------|------|-------------|
| mode | enum: "pause" \| "stop_token" | Active speech completion mode |
| stop_word | str | Keyword that signals end-of-turn in stop-token mode (default: "over") |
| max_timeout_s | float | Maximum listen duration regardless of mode (default: 60.0) |

**State transitions**: `pause ↔ stop_token` (toggled via `/mode` command or MCP tool)

**Scope**: Global (one instance per server). Not per-session.

---

### AgentSession

Represents a connected coding agent CLI instance communicating with the voice server.

| Field | Type | Description |
|-------|------|-------------|
| session_id | str (UUID) | Unique identifier for this session |
| session_name | str | Display name (directory basename, auto-suffixed if collision) |
| client_type | enum: "claude" \| "codex" \| "other" | Which CLI client is running |
| directory | str | Absolute path to the working directory |
| voice_name | str \| None | Assigned TTS voice from pool (None if only session) |
| spawn_mode | enum: "interactive" \| "headless" | How the CLI was launched |
| process_pid | int \| None | OS PID of the spawned process (None if connected externally) |
| terminal_pid | int \| None | OS PID of the terminal emulator (None if headless) |
| mcp_session_id | str \| None | Streamable HTTP session ID for this client |
| status | enum: "starting" \| "connected" \| "working" \| "idle" \| "disconnected" | Current lifecycle state |
| started_at | float | Unix timestamp when the session was created |
| last_activity | float | Unix timestamp of most recent tool call |
| owning_user_id | str | Discord user ID who spawned this session |

**State transitions**:
```
starting → connected → working ↔ idle → disconnected
                                     ↘ disconnected
```

**Lifecycle**:
- `starting`: Process spawned, not yet connected via MCP
- `connected`: MCP handshake complete, awaiting first tool call
- `working`: Active turn in progress (tool calls being made)
- `idle`: Turn completed, waiting for new input
- `disconnected`: Process exited or MCP connection dropped

---

### MessageQueue

Per-session ordered queue of pending messages.

| Field | Type | Description |
|-------|------|-------------|
| session_id | str | Target session this queue belongs to |
| messages | list[QueuedMessage] | Ordered list of pending messages |
| max_depth | int | Maximum queue size (configurable, default: 20) |

---

### QueuedMessage

A single pending message in a session's queue.

| Field | Type | Description |
|-------|------|-------------|
| message_id | str (UUID) | Unique message identifier |
| direction | enum: "agent_to_user" \| "user_to_agent" | Who sent the message |
| sender_session | str \| None | Session ID of sender (None if from user) |
| content | str | Message text content |
| timestamp | float | Unix timestamp when queued |
| delivered | bool | Whether the message has been read out / delivered |

---

### VoicePool

Curated list of TTS voices available for session assignment.

| Field | Type | Description |
|-------|------|-------------|
| pool_voices | list[str] | Ordered list of voice profile names from VoiceProfileRegistry |
| assignments | dict[str, str] | Map of session_id → voice_name for active sessions |
| system_voice | str | Voice name reserved for switchboard announcements |

**Assignment logic**:
1. If only one session: use system default voice (no pool assignment)
2. If explicit voice requested and available: assign it
3. If explicit voice unavailable: next unassigned pool voice, then system default
4. If all pool voices assigned: reuse with warning

---

### RouterIntent

Structured output from the LLM router.

| Field | Type | Description |
|-------|------|-------------|
| intent | enum: "reply_current" \| "route_to_session" \| "cold_call" \| "navigation" | Classified intent |
| target_session | str \| None | Session name to route to (for route/cold_call intents) |
| message_content | str \| None | Extracted message content (for cold_call intent) |
| navigation_action | str \| None | Navigation command: "next", "skip", "list" (for navigation intent) |
| confidence | float | Router confidence score (0.0–1.0) |

---

### RouterConfig

Configuration for the LLM-powered intent router.

| Field | Type | Description |
|-------|------|-------------|
| enabled | bool | Whether the router is active (default: False) |
| backend | enum: "codex_oauth" \| "openrouter" \| "openai_compatible" | Which LLM backend to use |
| model | str | Model identifier (e.g., "gpt-4o-mini", "claude-haiku-4-5-20251001") |
| api_key | str \| None | API key (for openrouter/openai_compatible backends) |
| api_base_url | str \| None | Base URL for openai_compatible backend |
| codex_auth_path | str | Path to Codex OAuth credentials (default: ~/.codex/auth.json) |
| timeout_ms | int | Max time for router inference (default: 500) |

---

### SpawnConfig

Configuration for terminal spawning.

| Field | Type | Description |
|-------|------|-------------|
| default_cli | enum: "claude" \| "codex" | Default CLI to spawn (default: "claude") |
| terminal_override | str \| None | User-configured terminal emulator binary name |
| server_url | str | Voice agent HTTP endpoint URL (default: "http://127.0.0.1:8765/mcp") |

---

### SessionMetadata (read-only, external)

Represents a previous CLI session read from the filesystem. Not stored by the voice server — read on demand from Claude Code or Codex session files.

**Claude Code sessions** (from `~/.claude/projects/<encoded-path>/sessions-index.json`):

| Field | Type | Source |
|-------|------|--------|
| session_id | str (UUID) | `entries[].sessionId` |
| summary | str | `entries[].summary` |
| message_count | int | `entries[].messageCount` |
| created | str (ISO timestamp) | `entries[].created` |
| modified | str (ISO timestamp) | `entries[].modified` |
| git_branch | str \| None | `entries[].gitBranch` |
| project_path | str | `entries[].projectPath` |
| cli_type | literal "claude" | Derived from source |

**Codex sessions** (from `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, first line):

| Field | Type | Source |
|-------|------|--------|
| thread_id | str (UUID) | `payload.id` |
| cwd | str | `payload.cwd` |
| timestamp | str (ISO timestamp) | `payload.timestamp` |
| git_branch | str \| None | `payload.git.branch` (if present) |
| cli_version | str | `payload.cli_version` |
| cli_type | literal "codex" | Derived from source |

---

### InitConfig

Persistent configuration written by the init wizard.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| discord_token | str | — | Discord bot token (required) |
| anthropic_api_key | str | — | Anthropic API key for STT correction |
| tts_backend | str | "local" | "local" or "elevenlabs" |
| tts_voice | str | "Ryan" | Default voice profile name |
| elevenlabs_api_key | str | "" | ElevenLabs API key (if backend=elevenlabs) |
| elevenlabs_voice_id | str | "" | ElevenLabs voice ID |
| whisper_model | str | "base" | Whisper model size |
| speech_mode | str | "pause" | Default speech completion mode |
| stop_word | str | "over" | Stop word for stop-token mode |
| default_cli | str | "claude" | Default CLI for /spawn |
| terminal_override | str | "" | Terminal emulator override |
| server_host | str | "127.0.0.1" | HTTP server bind address |
| server_port | int | 8765 | HTTP server bind port |
| router_enabled | bool | False | Enable LLM router |
| router_backend | str | "" | Router LLM backend |
| router_model | str | "" | Router LLM model |

**Storage**: `~/.config/voice-agent/config.env` (dotenv format)

## Relationships

```
InitConfig (written by init wizard)
    └── read by → Config (server startup)

Config
    ├── SpeechMode (global, one instance)
    ├── SpawnConfig
    ├── RouterConfig
    └── VoicePool
         └── references → VoiceProfileRegistry (existing)

AgentSession (1 per connected CLI client)
    ├── has 1 → MessageQueue
    ├── assigned from → VoicePool
    └── tracked by → SessionManager

SessionManager (singleton)
    ├── manages many → AgentSession
    ├── orchestrates → Switchboard
    └── uses → SpeechMode

Switchboard (singleton)
    ├── routes via → Router (optional)
    ├── reads → MessageQueue (per session)
    └── uses → VoicePool (for TTS voice selection)

SessionMetadata (read-only, on-demand)
    └── read by → SessionBrowser
```
