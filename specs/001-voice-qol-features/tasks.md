# Tasks: Voice QoL Features

**Input**: Design documents from `/specs/001-voice-qol-features/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks grouped by user story for independent implementation. Designed for parallel agent execution — see Parallel Opportunities section.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Add dependencies and create directory structure for all new modules

- [x] T001 Add starlette, uvicorn, thefuzz, and python-Levenshtein to dependencies in pyproject.toml; add `voice-agent` console script entry point (`server.init:main`) and `voice-agent-serve` entry point (`server.main:main`)
- [x] T002 [P] Create directory structure: `server/init/__init__.py`, `server/hooks/`, `tests/unit/`, `tests/integration/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extend shared config with all new fields needed across every user story

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Extend `server/config.py` with new dataclasses and fields: `SpeechModeConfig` (mode, stop_word, max_timeout_s), `SpawnConfig` (default_cli, terminal_override, server_url), `RouterConfig` (enabled, backend, model, api_key, api_base_url, codex_auth_path, timeout_ms), `ServerConfig` (host, port, transport), voice pool fields (voice_pool list, system_voice name), max_queue_depth. Update `Config.from_env()` to load all new env vars. Add fallback config path `~/.config/voice-agent/config.env` when no local `.env` exists.

**Checkpoint**: Config foundation ready — user story implementation can begin

---

## Phase 3: US1 + US2 — Speech Modes (Priority: P1) MVP

**Goal**: Implement stop-token speech completion and mode toggling so the user can speak with natural pauses and say "over" to signal turn end

**Independent Test**: Join a voice call, toggle to stop-token mode, speak with pauses, say stop word, verify transcript is captured correctly. Toggle back to pause mode and verify silence detection resumes.

### Implementation

- [x] T004 [P] [US1] Create `server/speech_mode.py`: `SpeechMode` dataclass (mode enum, stop_word, max_timeout_s), `SpeechModeManager` class with `get_mode()`, `set_mode()`, `is_stop_token()`, and `check_stop_word(transcript: str) -> tuple[bool, str]` that checks if transcript ends with the stop word (case-insensitive, strip punctuation) and returns (found, cleaned_transcript)
- [x] T005 [US1] Extend `server/stt_pipeline.py` `listen()` method: add `speech_mode` parameter. In stop-token mode, instead of returning on first `SpeechEvent(type="end")`, accumulate segments — transcribe each segment, check for stop word via `SpeechModeManager.check_stop_word()`, continue listening if not found, concatenate all segment transcripts when stop word detected or timeout reached. Reset VAD between segments. Strip stop word from final transcript.
- [x] T006 [US2] Add `/mode` and `/stopword` Discord slash commands in `server/discord_bot.py`: `/mode mode:<pause|stop_token> [stop_word:<string>]` sets the global speech mode; `/stopword word:<string>` updates the stop word. Both return ephemeral responses. Store reference to `SpeechModeManager` on the bot instance.
- [x] T007 [US2] Add `set_speech_mode` MCP tool: register tool schema in `server/main.py` `_TOOLS` list, add dispatch case in `_dispatch()`, route to `SpeechModeManager.set_mode()`. Return current mode and stop word.
- [x] T008 [US1] Wire `SpeechModeManager` into server startup in `server/main.py`: create instance from config, pass to `CallManager` (or future `SessionManager`), pass to `discord_bot` for slash commands. Update `CallManager._stt_listen()` to pass current speech mode to `STTPipeline.listen()`.

**Checkpoint**: Speech modes fully functional — stop-token mode works, toggling works via Discord command and MCP tool

---

## Phase 4: US3 — Auto-Detect Voice Channel (Priority: P1)

**Goal**: Eliminate hardcoded channel IDs — the bot finds the user in whatever voice channel they're currently in

**Independent Test**: Join any voice channel, issue a command without specifying channel ID, verify bot joins the correct channel. Move channels, verify bot follows.

### Implementation

- [x] T009 [P] [US3] Add `find_user_voice_channel(guild_id: int, user_id: int) -> int | None` method to `VoiceBot` in `server/discord_bot.py`: iterate guild's voice channels, find the channel containing the target non-bot user, return channel ID or None
- [x] T010 [US3] Update `_dispatch()` in `server/main.py` for `initiate_call`: when `channel_id` is omitted AND `default_channel_id` is None, resolve the user's voice channel via `find_user_voice_channel()`. Need to track the owning user from the MCP session or Discord interaction context. If user not in any channel, return clear error.
- [x] T011 [US3] Update `initiate_call` tool schema in `server/main.py` `_TOOLS`: update description to mention auto-detection. Add `session_name` optional property per contract.

**Checkpoint**: Auto-detect works — no channel_id needed when user is in a voice channel

---

## Phase 5: US10 — First-Time Setup Wizard (Priority: P1)

**Goal**: One-command guided setup for all defaults, MCP registration, and daemon installation

**Independent Test**: Run `voice-agent init` on a fresh config, complete wizard, verify config file written, MCP registered, daemon installed and running.

### Implementation

- [x] T012 [P] [US10] Create `server/init/wizard.py`: interactive setup wizard using `input()` with argparse for non-interactive `--flags`. Steps: Discord token, TTS backend, voice, CLI default, speech mode, stop word, Whisper model, terminal emulator. Pre-fill from existing config on re-run. Write to `~/.config/voice-agent/config.env`. Validate Discord token via lightweight API call.
- [x] T013 [P] [US10] Create `server/init/mcp_register.py`: detect installed CLIs (`which claude`, `which codex`). For Claude Code: run `claude mcp add --transport http --scope user voice-agent http://127.0.0.1:{port}/mcp` or write to `~/.claude.json` directly. For Codex: append `[mcp_servers.voice-agent]` to `~/.codex/config.toml`. Check for existing entries to avoid duplicates.
- [x] T014 [P] [US10] Create `server/init/systemd.py`: check systemd availability (`/run/systemd/system`), generate user unit file from template (WorkingDirectory, ExecStart with venv python path, Restart=always, RestartSec=10), write to `~/.config/systemd/user/voice-agent.service`, run `systemctl --user daemon-reload && enable && start`. Fall back gracefully on non-systemd systems.
- [x] T015 [US10] Create `server/init/__init__.py` with `main()` entry point: orchestrate wizard flow (wizard → mcp_register → systemd → summary). Wire to console script entry point in pyproject.toml (done in T001).

**Checkpoint**: `voice-agent init` fully functional — new users can set up in one command

---

## Phase 6: US4 — Persistent Multi-Client HTTP Server (Priority: P2)

**Goal**: Migrate from stdio to Streamable HTTP transport so multiple MCP clients can connect simultaneously

**Independent Test**: Start server with `--transport http`, connect two separate MCP clients, verify both can call tools independently. Disconnect one, verify server and other client continue.

**CRITICAL**: This phase BLOCKS US5, US6, US7, US8, US9

### Implementation

- [x] T016 [P] [US4] Create `server/http_app.py`: Starlette ASGI app with `StreamableHTTPSessionManager` wrapping the MCP `Server` instance. Mount on `/mcp` route handling GET and POST. Configure host/port from `ServerConfig`. Include health check endpoint at `/health`.
- [x] T017 [US4] Create `server/session_manager.py`: refactor from `server/call_manager.py`. Replace single `_sessions: dict[str, CallSession]` with multi-session registry using `AgentSession` dataclass. Track `mcp_session_id` → `AgentSession` mapping. Maintain `CallSession` compatibility for voice operations. Add `register_session()`, `unregister_session()`, `get_session_by_mcp_id()`, `list_active_sessions()`, auto-name sessions from directory basename with collision auto-suffix.
- [x] T018 [US4] Update `server/main.py` for dual-transport startup: add `--transport` CLI flag (default "http"). In HTTP mode: create Starlette app from `http_app.py`, run with uvicorn. In stdio mode: use existing `mcp.server.stdio.stdio_server()`. Both modes share the same `Server` instance, `SessionManager`, Discord bot thread, and TTS/STT pipelines. Add `voice-agent serve` entry point.
- [x] T019 [US4] Add `list_sessions` MCP tool: register schema in `_TOOLS`, dispatch to `SessionManager.list_active_sessions()`. Return session list per contract (session_id, name, client_type, directory, voice, status, spawn_mode, started_at, queued message info).
- [x] T020 [US4] Migrate tool handlers from `CallManager` to `SessionManager`: update `_dispatch()` to use `SessionManager` methods. Ensure `initiate_call`, `continue_call`, `speak_to_user`, `end_call` work through the new session registry. Keep backward compatibility with existing `call_id` parameter.

**Checkpoint**: HTTP transport works — multiple clients connect simultaneously, stdio retained as fallback

---

## Phase 7: US5 — Spawn Coding Agent (Priority: P2)

**Goal**: `/spawn /path/to/project` opens a terminal with a coding agent that calls the user back over voice

**Independent Test**: Issue `/spawn /path/to/project` in Discord, verify terminal opens, agent CLI starts, agent calls back over voice within 30 seconds.

**Depends on**: Phase 6 (US4 — HTTP server must be running for spawned agents to connect)

### Implementation

- [x] T021 [P] [US5] Create `server/spawn.py`: `TerminalDetector` class with detection chain (config override → `x-terminal-emulator` → `$TERMINAL` → PATH scan for gnome-terminal, konsole, kitty, alacritty, wezterm, foot, xterm). `SpawnManager` class: `spawn_session(directory, cli, voice, headless, user_id) -> AgentSession`. Validate directory exists, CLI binary in PATH. Build CLI launch command with MCP config pointing to voice server HTTP endpoint. Launch via `subprocess.Popen` in detected terminal (or headless). Track PID. Per-emulator launch flags (gnome-terminal `--`, kitty no `-e`, wezterm `start --`).
- [x] T022 [US5] Add `/spawn` Discord slash command in `server/discord_bot.py`: parameters `directory` (required), `cli` (optional, default from config), `voice` (optional), `headless` (optional bool). Validate inputs, call `SpawnManager.spawn_session()`, respond with ephemeral message. Register `SpawnManager` reference on bot. Wire user's voice channel at spawn time for callback target.
- [x] T023 [US5] Wire spawn into server startup in `server/main.py`: create `SpawnManager` with config, pass to `SessionManager` and `discord_bot`. Ensure spawned agent's `initiate_call` auto-detects user's voice channel (leveraging US3).

**Checkpoint**: `/spawn` works — terminal opens, agent starts, calls user back

---

## Phase 8: US6 — Per-Session Voice Assignment (Priority: P2)

**Goal**: Each concurrent session gets a distinct TTS voice so the user can tell agents apart by sound

**Independent Test**: Spawn two sessions, verify each uses a different voice. Check single-session uses default voice without pool assignment.

**Depends on**: Phase 6 (US4 — multi-session registry)

### Implementation

- [x] T024 [P] [US6] Create `server/voice_pool.py`: `VoicePool` class wrapping `VoiceProfileRegistry`. Initialize with curated pool voices from config (or sensible defaults from English preset speakers: Ryan, Aiden). Track assignments (session_id → voice_name). Methods: `assign_voice(session_id, requested_voice=None) -> str`, `release_voice(session_id)`, `get_system_voice() -> str`. Assignment logic: single session = default voice; explicit request honored if available; else next unassigned; all used = reuse with warning.
- [x] T025 [US6] Integrate `VoicePool` into `SessionManager` in `server/session_manager.py`: on session registration, assign voice from pool. On session removal, release voice. Pass assigned voice to TTS calls. When only one session active, skip pool. Wire system voice for switchboard use.

**Checkpoint**: Concurrent sessions have distinct voices — user can tell them apart

---

## Phase 9: US7 — Voice Switchboard with LLM Router (Priority: P2)

**Goal**: Multi-session message routing — session name prefixes, queue announcements via System Voice, LLM-powered intent classification, cold call delivery

**Independent Test**: Two agents send messages. System reads each in assigned voice with name prefix. System Voice announces queued messages. User replies are routed to correct session. Cold call messages delivered to busy agents.

**Depends on**: Phase 6 (US4), Phase 8 (US6 — voice pool for System Voice and per-session voices)

### Implementation

- [x] T026 [P] [US7] Create `server/switchboard.py`: `MessageQueue` class (per-session, max depth from config). `Switchboard` class: `enqueue_agent_message(session_id, content)`, `enqueue_user_message(session_id, content)` (cold call), `get_pending_announcements() -> list[QueuedMessage]`, `deliver_next_message(session_id) -> QueuedMessage | None`. Before listening, check queue and announce via System Voice TTS. Prefix messages with session name when multiple sessions active. Track last-speaker session for default routing.
- [x] T027 [P] [US7] Create `server/router.py`: `RouterIntent` dataclass (intent enum, target_session, message_content, navigation_action, confidence). `IntentRouter` class: `classify(transcript, active_sessions) -> RouterIntent`. Build prompt with active session names and 4 intent definitions. Call LLM via httpx (OpenAI-compatible chat completions with tool-calling). Support backends: openrouter (API key), codex_oauth (read `~/.codex/auth.json` JWT), openai_compatible (custom base URL). Fuzzy session name matching via `thefuzz.process.extractOne` with `token_sort_ratio` scorer, threshold 75. Fallback to `reply_current` on timeout/error.
- [x] T028 [P] [US7] Create `server/check_messages.py`: `check_messages` MCP tool implementation. Query `Switchboard` for pending user→agent messages for the calling session. Return as structured JSON per contract. Mark messages as delivered.
- [x] T029 [US7] Add `check_messages` MCP tool to `server/main.py`: register tool schema in `_TOOLS`, add dispatch case routing to `check_messages.py` handler. Pass `Switchboard` reference.
- [x] T030 [US7] Create `server/hooks/check_voice_queue.sh`: PostToolUse hook script for Claude Code. Check message queue file/socket (e.g., `/tmp/voice-agent-queue-{session_id}`). If messages exist, output JSON with `additionalContext` telling agent to call `check_messages`. If no messages, output empty JSON (zero overhead). Make executable.
- [x] T031 [US7] Integrate switchboard into `SessionManager` in `server/session_manager.py`: update `continue_call` flow to check switchboard queue before listening, announce pending messages via System Voice TTS, route user replies through `IntentRouter` (if enabled) or default to last-speaker. Update `initiate_call` to register with switchboard. Update `end_call` to drain and clean up session queue.
- [x] T032 [US7] Add `/mode` router configuration: when router is enabled, the `/mode` command or config controls it. No new slash command needed — router is server config. Ensure FIFO fallback when router disabled.

**Checkpoint**: Full switchboard operational — multi-session routing, cold calls, System Voice announcements

---

## Phase 10: US8 — Manage Spawned Sessions (Priority: P3)

**Goal**: List and terminate spawned agent sessions via Discord commands

**Independent Test**: Spawn agents, use `/sessions` to list them, use `/kill` to terminate one, verify process and terminal closed.

**Depends on**: Phase 7 (US5 — spawn must exist to manage)

### Implementation

- [x] T033 [P] [US8] Add `/sessions` (no-flags = active) and `/kill` Discord slash commands in `server/discord_bot.py`: `/sessions` with no parameters lists active spawned sessions from `SessionManager.list_active_sessions()` showing name, client type, voice, status, start time, queued messages. `/kill session:<string>` terminates a session by name or ID — calls `SpawnManager.kill_session()` which sends SIGTERM to process, kills terminal PID, removes from session registry, releases voice.
- [x] T034 [US8] Add `kill_session(session_name_or_id)` to `server/spawn.py` `SpawnManager`: find session by name (fuzzy match) or ID, send SIGTERM to process PID, kill terminal PID if interactive, call `SessionManager.unregister_session()`. Handle already-exited processes gracefully.

**Checkpoint**: Session management works — users can see and kill spawned agents

---

## Phase 11: US9 — Browse and Resume Previous Sessions (Priority: P3)

**Goal**: Browse previous Claude Code/Codex sessions from any project and resume them over voice

**Independent Test**: Run a normal Claude Code session, then use `/sessions --directory /path` to find it, use `/resume <id>` to relaunch in a terminal with voice callback.

**Depends on**: Phase 7 (US5 — spawn infrastructure for terminal launching and voice callback)

### Implementation

- [x] T035 [P] [US9] Create `server/session_browser.py`: `SessionBrowser` class. `list_claude_sessions(directory: str) -> list[SessionMetadata]`: encode path (`/` → `-`, prepend `-`), read `~/.claude/projects/<encoded>/sessions-index.json`, parse entries into `SessionMetadata` dataclass. `list_codex_sessions(directory: str | None) -> list[SessionMetadata]`: glob `~/.codex/sessions/*/*/*/rollout-*.jsonl`, read first line of each, parse `session_meta` payload, filter by `cwd` if directory provided. `list_recent(n: int, cli_filter: str | None) -> list[SessionMetadata]`: merge both sources, sort by timestamp desc, return top N. `detect_cli(session_id: str) -> str`: check both storage locations to determine if Claude or Codex.
- [x] T036 [US9] Add `/sessions --directory` and `/sessions --recent` variants to `/sessions` Discord slash command in `server/discord_bot.py`: when `directory` parameter provided, call `SessionBrowser.list_claude_sessions()` (or codex if `--cli codex`). When `recent` parameter provided (default 10), call `SessionBrowser.list_recent()`. Format output per contract with session ID, summary, timestamp, git branch.
- [x] T037 [US9] Add `/resume` Discord slash command in `server/discord_bot.py`: parameter `session_id` (required), `voice` (optional), `headless` (optional). Use `SessionBrowser.detect_cli()` to determine CLI. Build resume command: `claude -r "<sessionId>"` for Claude Code, `codex resume <threadId>` for Codex. Launch via `SpawnManager.spawn_session()` with resume flag. Agent callbacks with restored context.
- [x] T038 [US9] Handle edge cases in `server/session_browser.py`: corrupted JSONL files (catch JSON parse errors, skip), missing `~/.claude/` or `~/.codex/` directories (return empty list, no crash), deleted project directories (warn but include session).

**Checkpoint**: Session browsing and resume works — users can find and resume any previous session

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: Integration testing, edge case hardening, documentation

- [x] T039 Update `.env.example` with all new environment variables (SPEECH_MODE, STOP_WORD, DEFAULT_CLI, TERMINAL_EMULATOR, SERVER_HOST, SERVER_PORT, ROUTER_ENABLED, ROUTER_BACKEND, ROUTER_MODEL, ROUTER_API_KEY, MAX_QUEUE_DEPTH, VOICE_POOL, SYSTEM_VOICE)
- [x] T040 [P] Update `.mcp.json` to include HTTP transport option alongside existing stdio config
- [x] T041 [P] Update `CLAUDE.md` with new architecture overview, new modules, new env vars, new commands, and updated running instructions
- [x] T042 Validate all edge cases from spec: stop word mid-sentence, missing CLI binary, no terminal emulator, invalid directory, user leaves voice channel during callback, queue overflow, router timeout fallback, duplicate session names, corrupted session files
- [x] T043 Run `specs/001-voice-qol-features/quickstart.md` validation: verify setup instructions, run commands, confirm server starts in both transport modes

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) ────────────────→ Phase 2 (Foundational/Config)
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              Phase 3 (US1+2)    Phase 4 (US3)      Phase 5 (US10)
              Speech Modes       Auto-Detect         Init Wizard
              [P1, parallel]     [P1, parallel]      [P1, parallel]
                    │                  │
                    └────────┬─────────┘
                             ▼
                       Phase 6 (US4)
                       HTTP Server
                       [P2, BLOCKS]
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
              Phase 7 (US5)     Phase 8 (US6)
              Spawn Agent       Voice Pool
              [P2, parallel]    [P2, parallel]
                    │                 │
                    │    ┌────────────┘
                    │    ▼
                    │  Phase 9 (US7)
                    │  Switchboard
                    │  [P2]
                    │    │
              ┌─────┴────┤
              ▼           ▼
        Phase 10 (US8) Phase 11 (US9)
        Session Mgmt   Browse/Resume
        [P3, parallel]  [P3, parallel]
              │           │
              └─────┬─────┘
                    ▼
              Phase 12 (Polish)
```

### User Story Dependencies

- **US1+US2 (P1)**: After Foundational — no story dependencies
- **US3 (P1)**: After Foundational — no story dependencies
- **US10 (P1)**: After Foundational — no story dependencies
- **US4 (P2)**: After Foundational — BLOCKS US5, US6, US7, US8, US9
- **US5 (P2)**: After US4 — provides spawn infrastructure for US8, US9
- **US6 (P2)**: After US4 — provides voice pool for US7
- **US7 (P2)**: After US4 + US6 — switchboard needs voice pool for System Voice
- **US8 (P3)**: After US5 — manages spawned sessions
- **US9 (P3)**: After US5 — uses spawn infrastructure for resume

### Within Each Phase

- Config/dataclass tasks before service logic
- Service logic before integration wiring
- Discord commands after core logic is implemented
- MCP tool registration after handler implementation

---

## Parallel Opportunities

### Wave 1: Three P1 stories in parallel (3 agents)

After Phase 2 completes, launch simultaneously:

```
Agent A: Phase 3 (US1+US2) — speech_mode.py, stt_pipeline.py, /mode, /stopword, set_speech_mode tool
Agent B: Phase 4 (US3) — discord_bot.py auto-detect, main.py dispatch update
Agent C: Phase 5 (US10) — init/ subpackage, wizard.py, mcp_register.py, systemd.py
```

**File ownership** (no conflicts):
- Agent A owns: `server/speech_mode.py`, modifies `server/stt_pipeline.py`, `server/main.py` (tool schemas + dispatch)
- Agent B owns: modifies `server/discord_bot.py` (new method), modifies `server/main.py` (dispatch logic)
- Agent C owns: `server/init/` directory

**Conflict zone**: Both A and B modify `server/main.py`. Resolution: Agent A adds tool schemas/dispatch for `set_speech_mode`, Agent B modifies `initiate_call` dispatch logic. These are non-overlapping sections of the file but should be coordinated.

### Wave 2: Two P2 stories in parallel (2 agents)

After Phase 6 (US4) completes:

```
Agent D: Phase 7 (US5) — spawn.py, /spawn command
Agent E: Phase 8 (US6) — voice_pool.py, session_manager.py integration
```

**File ownership** (no conflicts):
- Agent D owns: `server/spawn.py`, modifies `server/discord_bot.py` (/spawn command)
- Agent E owns: `server/voice_pool.py`, modifies `server/session_manager.py` (voice assignment)

### Wave 3: Switchboard (1 agent, depends on Wave 2)

```
Agent F: Phase 9 (US7) — switchboard.py, router.py, check_messages.py, hooks/
```

### Wave 4: Two P3 stories in parallel (2 agents)

After Wave 3 completes:

```
Agent G: Phase 10 (US8) — /sessions active, /kill
Agent H: Phase 11 (US9) — session_browser.py, /sessions directory+recent, /resume
```

**File ownership**:
- Agent G modifies: `server/discord_bot.py` (/sessions no-flags, /kill), `server/spawn.py` (kill_session)
- Agent H owns: `server/session_browser.py`, modifies `server/discord_bot.py` (/sessions variants, /resume)

**Conflict zone**: Both modify `server/discord_bot.py` for different slash commands. These add separate `@self.tree.command` entries and don't overlap, but should be coordinated.

---

## Implementation Strategy

### MVP First (P1 Stories Only)

1. Phase 1: Setup (T001–T002)
2. Phase 2: Foundational config (T003)
3. **Parallel**: Phase 3 (speech modes) + Phase 4 (auto-detect) + Phase 5 (init wizard)
4. **STOP and VALIDATE**: Stop-token mode works, auto-detect works, init wizard works
5. At this point the system is immediately more usable than today — natural speech, no channel ID hassle, easy setup

### Full Delivery

6. Phase 6: HTTP server (T016–T020) — unlocks multi-client
7. **Parallel**: Phase 7 (spawn) + Phase 8 (voice pool)
8. Phase 9: Switchboard (T026–T032)
9. **Parallel**: Phase 10 (session mgmt) + Phase 11 (browse/resume)
10. Phase 12: Polish (T039–T043)

---

## Notes

- 43 total tasks across 12 phases
- 10 user stories covered (US1–US10)
- Maximum parallelism: 3 agents during Wave 1 (P1 stories), 2 agents during Waves 2 and 4
- Critical path: Setup → Config → US4 (HTTP) → US5+US6 → US7 (Switchboard) → US8+US9 → Polish
- Shortest path to MVP: Setup → Config → US1+US2 (speech modes) = 8 tasks
- `server/main.py` and `server/discord_bot.py` are the most-touched files — coordinate agent ownership carefully
- No test tasks generated (not explicitly requested in spec)
