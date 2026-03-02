# Discord Slash Command Contracts: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01

## Existing Commands (unchanged)

### /correct

```
/correct wrong:<string> right:<string>
```

Add an STT correction for the current user. Ephemeral response.

### /corrections

```
/corrections
```

List all STT corrections for the current user. Ephemeral response.

---

## New Commands

### /spawn

Launch a coding agent CLI instance in a terminal on the host desktop.

```
/spawn directory:<string> [cli:<string>] [voice:<string>] [headless:<boolean>]
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| directory | Yes | string | — | Absolute path to the working directory |
| cli | No | string | configured default | CLI client: "claude" or "codex" |
| voice | No | string | next from pool | TTS voice profile name |
| headless | No | boolean | False | Run without a terminal window |

**Response** (ephemeral):
```
Spawning claude in /home/joe/myproject (voice: Aiden)...
Terminal opened. Agent will call you shortly.
```

**Error responses**:
- `Directory not found: /path/to/nowhere`
- `CLI not found: codex is not installed`
- `No terminal emulator available. Use headless mode or configure TERMINAL_EMULATOR.`

### /sessions

List and browse active and previous sessions.

```
/sessions [directory:<string>] [recent:<integer>] [cli:<string>]
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| (none) | — | — | — | List active voice sessions |
| directory | No | string | — | List previous sessions in this directory |
| recent | No | integer | 10 | List N most recent inactive sessions across all directories |
| cli | No | string | both | Filter by CLI: "claude" or "codex" |

**Behavior by flag combination**:
- `/sessions` — list active voice sessions (spawned and connected)
- `/sessions directory:/path/to/project` — list previous Claude Code sessions in that directory
- `/sessions directory:/path/to/project cli:codex` — list previous Codex sessions in that directory
- `/sessions recent:25` — list 25 most recent sessions across all projects (mixed Claude+Codex)
- `/sessions recent:10 cli:claude` — list 10 most recent Claude Code sessions only

**Response** (active sessions, ephemeral):
```
Active sessions (2):
1. myproject (claude, working) — voice: Ryan — started 15m ago
2. api-server (codex, idle) — voice: Aiden — started 45m ago — 1 queued message
```

**Response** (previous sessions from directory, ephemeral):
```
Previous sessions in /home/joe/myproject (claude):
1. abc123 — "Refactored auth module" — 42 messages — main — 2h ago
2. def456 — "Fixed payment bug" — 18 messages — fix/payments — yesterday
3. ghi789 — "Added tests" — 25 messages — main — 3 days ago
Use /resume <session-id> to resume.
```

**Response** (recent sessions, ephemeral):
```
Recent sessions (10):
1. [claude] abc123 — myproject — "Refactored auth" — 2h ago
2. [codex] thr_456 — api-server — 5h ago
3. [claude] def789 — frontend — "Added dark mode" — yesterday
...
Use /resume <session-id> to resume.
```

### /resume

Resume a previous CLI session in a new terminal with a voice callback.

```
/resume session_id:<string> [voice:<string>] [headless:<boolean>]
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| session_id | Yes | string | — | Session ID (Claude UUID or Codex thread ID) |
| voice | No | string | next from pool | TTS voice profile name |
| headless | No | boolean | False | Run without a terminal window |

**Response** (ephemeral):
```
Resuming claude session abc123 in /home/joe/myproject (voice: Ryan)...
Terminal opened. Agent will call you with restored context.
```

**Error responses**:
- `Session not found: xyz789`
- `Session data unavailable (file may have been deleted)`

### /kill

Terminate a spawned agent session.

```
/kill session:<string>
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| session | Yes | string | — | Session name or session ID |

**Response** (ephemeral):
```
Session myproject terminated. Process and terminal closed.
```

### /mode

Toggle speech completion mode.

```
/mode mode:<string> [stop_word:<string>]
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| mode | Yes | string | — | "pause" or "stop_token" |
| stop_word | No | string | "over" | Stop word (only for stop_token mode) |

**Response** (ephemeral):
```
Speech mode set to stop_token (stop word: "over")
```

### /stopword

Configure the stop word for stop-token mode.

```
/stopword word:<string>
```

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| word | Yes | string | — | New stop word |

**Response** (ephemeral):
```
Stop word updated to "done"
```
