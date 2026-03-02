# Feature Specification: Voice QoL Features

**Feature Branch**: `001-voice-qol-features`
**Created**: 2026-03-01
**Status**: Draft
**Input**: User description: "Add QoL features: toggle between pause-based and stop-token speech completion modes, and a slash command to spawn a coding agent CLI instance (Claude Code, Codex CLI, or other MCP-compatible client) in a visible terminal on the host desktop that calls the user for its initial prompt. Server migrates to HTTP/SSE transport for multi-client support with LLM-powered voice switchboard routing. Auto-detect user's voice channel instead of hardcoding channel IDs."

## Clarifications

### Session 2026-03-01

- Q: Should the voice pool be curated or auto-cycle through all available voices? → A: Curated pool with reasonable defaults for users who don't want to voice-train.
- Q: Should the session name prefix be spoken on every message or only the first message of each call? → A: Every message, but only when multiple sessions are active at once.
- Q: Should the ready-to-report queue auto-advance or let the user explicitly choose? → A: LLM-powered router parses user voice commands to determine routing. Default FIFO for users without router enabled. Router supports cold calls (voicemail to busy agents), multi-session routing, and navigation commands.
- Q: Should voice routing be limited to lobby-between-calls or also support mid-call switching? → A: Routing happens between messages, not between calls. After a message is read out and before listening for the reply, the system announces any queued messages. User can reply to current, listen to the next, or cold-call a specific session.
- Q: What happens when an agent hits a blocker while the user is on another call? → A: Don't wait for a call to end. Wait for the current message readout to end. Before listening for the reply, System Voice announces queued messages. User chooses: reply to current, or hear the queued voicemail.
- Q: Should the System Voice be similar to agent voices or distinctly different? → A: Noticeably different — a robotic/neutral tone, immediately distinguishable from the curated agent voices.
- Q: When no explicit session is named by the user, which session receives the reply? → A: Last speaker — the session whose message was most recently read out.
- Q: How should cold call messages be injected into a running agent session? → A: Server exposes a `check_messages` MCP tool. For Claude Code: a `PostToolUse` hook checks the queue — if messages exist, it nudges the agent (via `additionalContext`) to call `check_messages`. No messages = no overhead. For Codex CLI: `turn/steer` via App Server for direct injection, plus `check_messages` as universal fallback. Message content comes back as a proper tool result (real conversation content, not background context).
- Q: Should the HTTP/SSE endpoint require authentication? → A: No auth, but bind to localhost only. All spawned agents run on the same machine, so localhost binding is sufficient. No token management needed.
- Q: What happens when two sessions have the same directory basename? → A: Auto-suffix duplicates (e.g., myproject, myproject-2).
- Q: Is the speech completion mode global or per-session? → A: Global — one mode active at a time. There's one user, one mic, one voice channel. Speech mode controls how the server listens, which is inherently global.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stop-Token Speech Completion Mode (Priority: P1)

As a user on a voice call with an AI agent, I want to be able to say a specific trigger word (e.g., "over") to signal that I'm done speaking, instead of relying on a long pause to end my turn. This lets me pause naturally mid-thought without the system cutting me off prematurely.

**Why this priority**: The current pause-based completion is the core interaction loop. When the silence threshold is short, natural pauses get misinterpreted as turn-endings and the user gets cut off mid-thought. When it's long, every turn feels sluggish. A stop-token mode eliminates this fundamental tension.

**Independent Test**: Can be fully tested by joining a voice call, enabling stop-token mode, speaking with natural pauses, saying the stop word, and verifying the system only processes the transcript after hearing the stop word. Delivers immediate value by making conversations feel more natural.

**Acceptance Scenarios**:

1. **Given** a user is on an active call with stop-token mode enabled, **When** the user speaks with natural pauses (1-3 seconds of silence mid-sentence), **Then** the system continues listening and does not treat the pause as a completed utterance.
2. **Given** a user is on an active call with stop-token mode enabled, **When** the user says the configured stop word (e.g., "over") at the end of their message, **Then** the system finalizes the transcript (excluding the stop word itself) and returns it.
3. **Given** a user is on an active call with stop-token mode enabled, **When** the user uses the stop word naturally in a sentence (e.g., "I went over the document"), **Then** the system does not prematurely end listening because the word appeared mid-speech rather than after a pause.
4. **Given** a user is on an active call, **When** the user has not spoken for the maximum timeout period (e.g., 60 seconds), **Then** the system ends listening regardless of which mode is active, as a safety fallback.

---

### User Story 2 - Toggle Between Speech Completion Modes (Priority: P1)

As a user, I want to toggle between "pause-based" and "stop-token" speech completion modes during or before a call, so I can choose the interaction style that suits my current situation.

**Why this priority**: Directly enables User Story 1 to be useful in practice. Without a toggle, users are locked into one mode. Some situations favor pause-based (quick back-and-forth), others favor stop-token (complex explanations).

**Independent Test**: Can be tested by switching modes and verifying each mode's behavior is active. Delivers value by giving users control over their interaction style.

**Acceptance Scenarios**:

1. **Given** the system defaults to pause-based mode, **When** a user changes the mode setting to stop-token, **Then** subsequent listen operations use stop-token detection instead of pause detection.
2. **Given** the system is in stop-token mode, **When** a user switches back to pause-based mode, **Then** subsequent listen operations use the original pause-based silence detection.
3. **Given** a mode is configured, **When** a new call is initiated, **Then** the call uses the currently configured mode.

---

### User Story 3 - Auto-Detect User Voice Channel (Priority: P1)

As a user, I want the system to automatically detect which voice channel I'm in when I issue a command, instead of requiring a hardcoded channel ID. If I issue a `/spawn` command or an agent needs to call me, the system should find me in whatever voice channel I'm currently in.

**Why this priority**: The current system requires a `channel_id` argument or a preconfigured `default_channel_id`. This is friction for every single interaction. Auto-detection removes a manual step from every call and is essential for the spawn flow where the bot needs to know where to call back.

**Independent Test**: Can be tested by joining any voice channel, issuing a command without specifying a channel, and verifying the bot joins the correct channel. Delivers value by eliminating configuration friction from every interaction.

**Acceptance Scenarios**:

1. **Given** a user is in a voice channel, **When** an agent initiates a call without specifying a channel ID, **Then** the system detects the user's current voice channel and joins it.
2. **Given** a user is in a voice channel and issues the `/spawn` command, **When** the spawned instance calls back, **Then** it joins the voice channel the user was in at the time of the spawn command.
3. **Given** a user is not in any voice channel, **When** an agent attempts to call them, **Then** the system reports that the user is not in a voice channel and cannot be reached.
4. **Given** a user moves to a different voice channel between issuing a command and the callback arriving, **When** the callback occurs, **Then** the system finds the user in their current channel (not the one they were in when the command was issued).
5. **Given** multiple non-bot users are in voice channels, **When** an agent needs to call a specific user, **Then** the system identifies the correct user's channel based on the user who triggered the interaction.

---

### User Story 4 - Persistent Multi-Client Server (Priority: P2)

As a system operator, I want the voice agent to run as a standalone persistent daemon using HTTP/SSE transport, so that multiple MCP-compatible clients (Claude Code, Codex CLI, or any other MCP client) can connect to it simultaneously and the server's lifecycle is independent of any single client session.

**Why this priority**: This is the foundational infrastructure that enables spawning (User Story 5) and the voice switchboard (User Story 6). The current stdio transport limits the server to a single client and ties its lifecycle to that client. Without this migration, no multi-client features are possible.

**Independent Test**: Can be tested by starting the server as a daemon, connecting two separate MCP clients to it via HTTP, and verifying both can issue tool calls independently. Delivers value by making the server robust and independently deployable.

**Acceptance Scenarios**:

1. **Given** the server is started as a standalone process, **When** any MCP-compatible client connects via HTTP/SSE, **Then** it can discover and call all available MCP tools exactly as it does today over stdio.
2. **Given** the server is running, **When** the first connected client disconnects, **Then** the server continues running and remains available for other clients.
3. **Given** the server is running, **When** multiple clients of different types (e.g., one Claude Code, one Codex CLI) are connected simultaneously, **Then** each client's tool calls are routed correctly and do not interfere with each other.
4. **Given** the server is running, **When** no clients are connected, **Then** the server continues running, the Discord bot stays connected, and the server accepts new client connections.

---

### User Story 5 - Spawn Coding Agent from Discord Command (Priority: P2)

As a user on a Discord server where the voice agent is active, I want to issue a slash command (e.g., `/spawn /home/joe/myproject`) that opens a new terminal window on my desktop running an interactive coding agent CLI (Claude Code, Codex CLI, etc.), pointed at a specified directory. That agent then calls me back over voice to receive its initial prompt and context, so I can brief it hands-free while watching it work in the terminal.

**Why this priority**: Delivers significant value for hands-free AI-assisted development workflows. The interactive terminal gives the user full visibility into what the agent is doing. Depends on the persistent multi-client server (User Story 4) being in place so the spawned instance can connect back.

**Independent Test**: Can be tested by issuing the spawn command, verifying a terminal window appears on the desktop with the agent running, and verifying the agent initiates a voice call back to the user within a reasonable time.

**Acceptance Scenarios**:

1. **Given** a user is in a Discord server where the bot is active, **When** the user issues the spawn slash command with a valid directory path, **Then** a new terminal window opens on the host desktop running the configured coding agent CLI in that directory.
2. **Given** a coding agent has been spawned in interactive mode, **When** it starts up and connects to the voice agent server, **Then** it initiates a voice call to the user who spawned it and introduces itself (e.g., "Hi, I've been launched in myproject. What would you like me to work on?").
3. **Given** a user is on a call with the spawned agent, **When** the user describes a task verbally, **Then** the spawned agent receives the transcript as its initial prompt and begins working — visible in the terminal window.
4. **Given** a user specifies an invalid or inaccessible directory path, **When** the spawn command is issued, **Then** the system reports the error clearly to the user without crashing.
5. **Given** a user specifies a different CLI client (e.g., `/spawn /path --cli codex`), **When** the spawn executes, **Then** the system launches the specified client instead of the default.
6. **Given** no terminal emulator override is configured, **When** the spawn command opens a terminal, **Then** it uses the system's default terminal emulator (detected via `x-terminal-emulator`, `$TERMINAL`, or known emulator scanning). If an override is configured (e.g., `wezterm`), the configured emulator is used instead.
7. **Given** a user is away from their desktop, **When** they issue the spawn command with a headless flag (e.g., `/spawn /path --headless`), **Then** the agent runs as a background process without a terminal window.

---

### User Story 6 - Per-Session Voice Assignment (Priority: P2)

As a user with multiple concurrent agent sessions, I want each session to have a distinct TTS voice so I can immediately tell which agent is speaking without waiting to hear the session name.

**Why this priority**: When multiple agents are sending messages, auditory differentiation is faster than verbal identification. A unique voice per session gives instant recognition, and combined with the spoken session name prefix, provides double identification (voice + name).

**Independent Test**: Can be tested by spawning two sessions, verifying each uses a different voice, and confirming the user can distinguish them by voice alone.

**Acceptance Scenarios**:

1. **Given** a session is spawned without a voice preference, **When** the session's agent speaks via TTS, **Then** it uses the next available voice from the curated voice pool, distinct from all other active sessions.
2. **Given** a session is spawned with an explicit voice (e.g., `/spawn /path --voice Ryan`), **When** the session speaks, **Then** it uses the requested voice.
3. **Given** a requested voice is not available (not installed, already in use by another session), **When** the session needs to speak, **Then** it falls back to the next available voice from the pool, then to the system default voice.
4. **Given** only one session is active, **When** it speaks, **Then** it uses the system default voice (no pool assignment needed).
5. **Given** more sessions are active than voices in the pool, **When** a new session needs a voice, **Then** the system reuses a voice from the pool (with a warning that voices are no longer unique) rather than failing.

---

### User Story 7 - Voice Switchboard with LLM Router (Priority: P2)

As a user managing multiple concurrent agent sessions, I want the system to act as an intelligent voice switchboard: reading out agent messages in the agent's assigned voice with a session name prefix, announcing queued messages between readouts, and routing my spoken replies to the correct session — including the ability to send "cold call" messages (voicemail) to sessions that are busy working.

**Why this priority**: This is what makes multi-agent voice interaction usable. Without intelligent routing, the user would need to explicitly address every message and navigate a rigid queue. The voice switchboard makes it conversational and hands-free.

**Independent Test**: Can be tested by having two agents send messages, verifying each is read in its assigned voice with a name prefix, verifying the system announces the second message after the first readout, and verifying the user's reply is routed to the correct agent.

**Acceptance Scenarios**:

1. **Given** an agent sends a message via the voice server, **When** the message is read out via TTS, **Then** it is spoken in the agent's assigned voice and prefixed with the session name (e.g., "myproject: I've finished the refactor...").
2. **Given** only one session is active, **When** messages are read out, **Then** no session name prefix is spoken (no ambiguity to resolve).
3. **Given** a message has just been read out and another message is queued from a different agent, **When** the system prepares to listen for the user's reply, **Then** a distinct System Voice first announces the queued message (e.g., "api-server also has a message waiting"), then listens for the user's response.
4. **Given** the router is enabled and the user replies after hearing a message from "myproject", **When** the user says "yes, go ahead" (implicit reply, no session named), **Then** the router routes the reply to myproject (the last speaker).
5. **Given** the router is enabled and the user says "tell api-server to hold off on that deploy", **When** the router parses this, **Then** it extracts the target session ("api-server") and the message content ("hold off on that deploy") and delivers the message to api-server — even if api-server is currently busy working and not listening (cold call / voicemail).
6. **Given** the router is enabled and the user says "next" or "hear api-server", **When** the router processes this, **Then** the system reads out api-server's queued message instead of routing a reply.
7. **Given** the router is NOT enabled (disabled or not configured), **When** multiple messages are queued, **Then** the system uses simple FIFO ordering and all replies go to the last speaker (no routing intelligence).
8. **Given** a cold call message is sent to a busy agent, **When** that agent next calls `continue_call` or checks for messages, **Then** it receives the user's queued message as if the user had said it live during a call.

---

### User Story 8 - Manage Spawned Agent Sessions (Priority: P3)

As a user, I want to see which coding agent instances I've spawned and be able to end them, so I maintain awareness and control over running processes on my host machine.

**Why this priority**: Supporting feature for User Story 5. Without session management, spawned processes could accumulate without the user's awareness.

**Independent Test**: Can be tested by spawning one or more instances, listing them, and terminating one, verifying the process is cleaned up.

**Acceptance Scenarios**:

1. **Given** one or more agent instances have been spawned, **When** the user requests a list of active sessions, **Then** the system displays each session's client type (Claude Code, Codex, etc.), directory, assigned voice, start time, spawn mode (interactive/headless), status, and whether it is currently on a call or has queued messages.
2. **Given** an active spawned session exists, **When** the user requests to end that session, **Then** the agent process (and its terminal window, if interactive) is terminated gracefully, its queued messages and voice assignment are released, and it is removed from the active list.

---

### User Story 9 - Browse and Resume Previous Sessions (Priority: P3)

As a user, I want to browse previous coding agent sessions — even ones that weren't started over voice — and resume any of them in a new terminal with a voice callback, so I can pick up where I left off hands-free.

**Why this priority**: Extends the spawn workflow to existing work. Users accumulate many Claude Code and Codex sessions across projects. Being able to resume any of them over voice turns the system into a universal "pick up where I left off" tool, not just a launcher for new work.

**Independent Test**: Can be tested by running a normal (non-voice) Claude Code or Codex session in a project, then using the `/sessions --directory` or `/sessions --recent` command to find it, and `/resume` to relaunch it in a terminal with a voice callback.

**Acceptance Scenarios**:

1. **Given** a user has previous Claude Code sessions in a directory, **When** they issue `/sessions --directory /path/to/project`, **Then** the system reads Claude Code's session index (`~/.claude/projects/<encoded>/sessions-index.json`) and displays a list of sessions with their summary, last modified time, message count, and git branch.
2. **Given** a user has previous Codex CLI sessions in a directory, **When** they issue `/sessions --directory /path/to/project --cli codex`, **Then** the system reads Codex session files (`~/.codex/sessions/*/*/*/rollout-*.jsonl` metadata lines) filtered by working directory and displays a list of sessions with their timestamp, thread ID, and git branch.
3. **Given** a user wants to see recent sessions across all projects, **When** they issue `/sessions --recent` (or `/sessions --recent 25`), **Then** the system reads both Claude Code and Codex session indexes, merges by timestamp, and displays the N most recent sessions (default 10, mixed by default).
4. **Given** a user wants recent sessions for a specific CLI only, **When** they issue `/sessions --recent --cli codex`, **Then** only Codex sessions are listed.
5. **Given** a user has identified a session to resume, **When** they issue `/resume <session-id>`, **Then** the system spawns a new terminal running the appropriate CLI (`claude -r "<sessionId>"` or `codex resume <threadId>`) pre-configured to connect to the voice server, and the resumed agent initiates a voice callback.
6. **Given** a user resumes a session, **When** the agent calls back over voice, **Then** the agent has its full previous conversation context restored and can continue from where it left off.
7. **Given** a session ID doesn't match any known session, **When** `/resume` is issued, **Then** the system reports a clear error.

---

### User Story 10 - First-Time Setup Wizard (Priority: P1)

As a new user, I want a single `init` CLI command that walks me through all one-time setup — configuring defaults, installing the MCP server into my coding agent CLIs, and setting up credentials — so I don't have to manually edit config files or figure out the setup order myself.

**Why this priority**: Without guided setup, every new user faces a wall of environment variables, config files, and MCP registration steps. This is the first thing anyone runs, and if it's painful, nothing else matters. The init command makes the system approachable.

**Independent Test**: Can be tested by running the init command on a fresh machine (or with no existing config), completing the wizard, and verifying the system starts and works correctly with the configured settings.

**Acceptance Scenarios**:

1. **Given** a user has never configured the voice agent, **When** they run the init command, **Then** it walks them through each configuration step interactively, with sensible defaults offered at every prompt.
2. **Given** the init wizard reaches the Discord bot token step, **When** the user needs help, **Then** the wizard provides brief instructions on how to create a Discord bot and obtain a token.
3. **Given** the user selects a TTS backend (local or cloud), **When** they choose ElevenLabs, **Then** the wizard prompts for the API key and voice ID; when they choose local, it prompts for voice selection from available profiles.
4. **Given** the user has Claude Code installed, **When** the init wizard reaches MCP registration, **Then** it offers to install the voice agent as an MCP server in Claude Code's configuration automatically.
5. **Given** the user has Codex CLI installed, **When** the init wizard reaches MCP registration, **Then** it offers to install the voice agent in Codex CLI's MCP configuration automatically.
6. **Given** a user has already run init previously, **When** they run init again, **Then** existing values are shown as defaults so the user can keep current settings or change specific ones without re-entering everything.
7. **Given** the init wizard reaches the daemon setup step, **When** the user opts in, **Then** the wizard installs a system service (systemd unit or equivalent) so the Discord bot and voice server start automatically on boot and restart on crash.
8. **Given** the daemon is configured, **When** the user finishes the wizard, **Then** the wizard offers to start the daemon immediately and confirms it is running.
9. **Given** the user completes the init wizard, **When** all steps are done, **Then** the wizard writes the configuration to disk, confirms what was configured, and shows a summary of next steps (e.g., "Server is running. Try `/spawn /path/to/project` in Discord.").
10. **Given** the user wants to skip the interactive wizard, **When** they pass flags directly (e.g., `--discord-token XXX --tts-backend local`), **Then** the system accepts flags for any setting and only prompts interactively for missing required values.

---

### Edge Cases

- What happens when the user says the stop word but the STT engine mishears it? The system should support configuring the stop word via the existing correction mechanism and consider common misrecognitions of the chosen stop word.
- What happens when the requested CLI client is not installed? The spawn command should verify the CLI binary exists in PATH before attempting to launch and report a clear error if not found.
- What happens when no terminal emulator can be detected and the user didn't configure an override? The system should fall back to headless mode and inform the user that no terminal was found.
- What happens if the configured terminal emulator override (e.g., `wezterm`) is not installed? The system should fall back to the system default detection chain and warn the user.
- What happens when the target directory does not exist? The system should validate the path before spawning and report the error.
- What happens if the user disconnects from voice while a spawned instance is calling back? The spawned instance should handle the call failure gracefully and its call request should remain queued until the user reconnects.
- What happens if the user says the stop word while the agent is still speaking (barge-in)? The system should only listen for the stop word during active listen phases, not during TTS playback.
- What happens if the call queue grows very large (e.g., 10+ waiting agents)? The system should enforce a configurable maximum queue depth and reject new requests with a clear message when the queue is full.
- What happens if a queued agent's MCP client disconnects while waiting? The system should detect the disconnection and remove the stale entry from the queue.
- What happens if the server is restarted while clients are connected? Connected clients should receive a clean disconnection. Spawned processes are orphaned and should be trackable for cleanup on next server start.
- What happens if the user is in multiple guilds and in voice channels in more than one? The system should resolve based on the guild where the triggering command was issued.
- What happens if the user leaves the voice channel between a spawn command and the callback? The system should report that the user is no longer reachable and queue the call until the user rejoins a voice channel.
- What happens if the router LLM is unreachable or too slow? The system should fall back to FIFO behavior (route reply to last speaker) and warn the user that routing is degraded.
- What happens if the router misparses a reply and routes it to the wrong session? The user should be able to correct this (e.g., "no, that was for myproject") and the system should re-route. The correction is also fed back to improve routing context.
- What happens if a cold-call message is sent to a session that has already been terminated? The system should inform the user that the session no longer exists.
- What happens if all voices in the curated pool are in use? The system should reuse voices (with a warning) rather than blocking the spawn.
- What happens if a user tries to resume a session whose JSONL file has been deleted or corrupted? The system should report that the session data is unavailable.
- What happens if `~/.claude/` or `~/.codex/` directories don't exist (CLI not installed)? The system should report no sessions found for that CLI, not crash.
- What happens if a resumed session's original project directory no longer exists? The system should warn the user but still attempt the resume (the CLI may handle missing directories gracefully).
- What happens if the user runs init but doesn't have permissions to install a systemd service? The wizard should detect this and offer alternatives (e.g., run as a regular process, create a user-level systemd unit instead of system-level).
- What happens if the MCP configuration file for a CLI tool already has a voice-agent entry? The wizard should detect the existing entry and offer to update it rather than creating a duplicate.
- What happens if the Discord token provided during init is invalid? The wizard should validate the token (e.g., attempt a lightweight API call) and prompt the user to re-enter if it fails.

## Requirements *(mandatory)*

### Functional Requirements

**Speech Completion Modes:**

- **FR-001**: System MUST support two speech completion modes: "pause" (current behavior, silence-duration triggers turn end) and "stop-token" (a spoken keyword signals turn end).
- **FR-002**: System MUST allow users to configure which speech completion mode is active, with "pause" as the default. The mode is global (applies to all listening, not per-session).
- **FR-003**: In stop-token mode, the system MUST continue accumulating audio across natural pauses until the stop word is detected or the maximum timeout is reached.
- **FR-004**: In stop-token mode, the system MUST strip the stop word from the final transcript before returning it.
- **FR-005**: The stop word MUST be configurable, with a sensible default (e.g., "over").
- **FR-006**: In stop-token mode, the system MUST distinguish between the stop word spoken mid-sentence (preceded and followed by speech) and the stop word spoken as a turn-ending signal (followed by silence).
- **FR-007**: The system MUST provide a mechanism for users to toggle speech completion mode, such as a Discord slash command, an MCP tool call, or an environment variable for the default.

**Auto-Detect Voice Channel:**

- **FR-008**: When initiating a call, the system MUST be able to automatically detect the target user's current voice channel instead of requiring an explicit channel ID.
- **FR-009**: If the target user is not in any voice channel, the system MUST report a clear error rather than failing silently or joining a default channel.
- **FR-010**: The system MUST resolve the user's voice channel at call time (not at command time), so that if the user moves channels between a command and a callback, the system finds them in their current location.
- **FR-011**: The hardcoded/configured default channel ID MUST remain as an optional fallback for headless or non-interactive scenarios where no user context is available.

**Persistent Multi-Client Server:**

- **FR-012**: The server MUST support HTTP/SSE transport as an alternative to stdio, allowing multiple MCP clients to connect simultaneously.
- **FR-013**: The server MUST run as a standalone persistent process whose lifecycle is independent of any connected client.
- **FR-014**: The server MUST maintain client session isolation — tool calls from one client MUST NOT interfere with another client's state.
- **FR-015**: The server MUST continue running and accepting new connections when clients connect or disconnect.
- **FR-015a**: The HTTP/SSE endpoint MUST bind to localhost only by default, restricting connections to processes on the same machine.

**Spawn and Session Management:**

- **FR-016**: System MUST support a spawn command that launches a coding agent CLI process on the host machine in a user-specified directory. The default client is configurable, with Claude Code as the default.
- **FR-017**: The spawn command MUST support choosing which CLI client to launch (e.g., `claude`, `codex`). Any MCP-compatible CLI that accepts an MCP server configuration and an initial prompt is a valid client.
- **FR-018**: The spawned agent MUST be pre-configured to connect to the running voice agent server via HTTP/SSE so it can initiate calls.
- **FR-019**: The spawned agent MUST automatically initiate a voice call to the user after startup to receive its initial prompt.
- **FR-020**: The spawn command MUST support two modes: "interactive" (default) opens a visible terminal window on the host desktop; "headless" runs the agent as a background process.
- **FR-021**: In interactive mode, the system MUST detect and use the system's default terminal emulator. The detection chain is: configured override (e.g., `wezterm`) > `x-terminal-emulator` symlink > `$TERMINAL` environment variable > scanning PATH for known emulators.
- **FR-022**: The terminal emulator override MUST be user-configurable (e.g., via environment variable or server config).
- **FR-023**: System MUST track spawned agent sessions and allow users to list and terminate them.
- **FR-024**: System MUST validate the target directory exists and is accessible before attempting to spawn a process.
- **FR-025**: System MUST verify the requested CLI binary exists in PATH before attempting to launch.

**Per-Session Voice Assignment:**

- **FR-026**: Each spawned session MUST be assigned a distinct TTS voice from a curated voice pool, so concurrent sessions sound different.
- **FR-027**: The voice pool MUST include reasonable default voices that work without voice training or custom setup.
- **FR-028**: Users MUST be able to explicitly assign a voice to a session at spawn time (e.g., `--voice Ryan`).
- **FR-029**: If the requested voice is unavailable, the system MUST fall back to the next available pool voice, then to the system default voice.
- **FR-030**: When only one session is active, it MUST use the system default voice (no pool assignment overhead).
- **FR-031**: When more sessions are active than pool voices, the system MUST reuse voices with a warning rather than failing.

**Voice Switchboard and Message Routing:**

- **FR-032**: When multiple sessions are active, every TTS message readout MUST be prefixed with the session name (derived from the working directory basename, or a user-assigned label). If a basename collides with an existing active session, the system MUST auto-suffix it (e.g., "myproject-2").
- **FR-033**: When only one session is active, messages MUST NOT be prefixed with the session name.
- **FR-034**: A distinct "System Voice" with a noticeably robotic/neutral tone (separate from and immediately distinguishable from all agent voices) MUST be used for routing announcements, queue notifications, and switchboard prompts.
- **FR-035**: After a message is read out via TTS, and before the system listens for the user's reply, the system MUST check for queued messages from other sessions. If any exist, the System Voice MUST announce them (e.g., "api-server also has a message waiting").
- **FR-036**: The system MUST support an LLM-powered router that parses the user's spoken reply to determine intent: implicit reply (to last speaker), explicit routing (to a named session), cold call (voicemail to a busy session), or navigation command ("next", "skip", "list sessions").
- **FR-037**: The router MUST be optional and disabled by default. When disabled, all replies route to the last speaker — the session whose message was most recently read out to the user.
- **FR-038**: The router MUST support configurable LLM backends: Codex OAuth (reusing the user's existing Codex CLI credentials), OpenRouter API key, or any OpenAI-compatible endpoint.
- **FR-039**: Cold call messages (voicemail) MUST be queued and delivered to the target session when it next calls `continue_call` or checks for messages.
- **FR-040**: The router MUST use fuzzy matching for session names to handle STT transcription errors (e.g., "my project" matching "myproject").
- **FR-041**: If the router cannot confidently parse the user's intent, it MUST fall back to routing the reply to the last speaker and optionally asking for clarification.
- **FR-042**: System MUST enforce a configurable maximum message queue depth and reject new requests when the queue is full.
- **FR-043**: The server MUST expose a `check_messages` MCP tool that returns any queued voice messages (cold calls/voicemail) for the calling session. Messages are returned as proper tool results so the agent processes them as real conversation content.
- **FR-044**: For Claude Code sessions, a `PostToolUse` hook MUST check the voice server's message queue (via shared file or socket). If messages are queued, the hook MUST return `additionalContext` instructing the agent to call `check_messages`. If no messages are queued, the hook MUST return nothing (zero overhead).
- **FR-045**: For Codex CLI sessions, the system MUST spawn `codex app-server` (JSON-RPC interface) rather than `codex` directly, to enable mid-turn message injection via `turn/steer` and real-time progress monitoring via notifications. The `check_messages` MCP tool also works as a universal fallback.
- **FR-046**: Spawned agents' system prompts (via `--append-system-prompt` or equivalent) MUST instruct them to call `check_messages` when notified of pending voice messages.

**Session Browsing and Resume:**

- **FR-047**: The `/sessions` command with no flags MUST list active voice sessions (currently spawned and connected).
- **FR-048**: `/sessions --directory <path>` MUST list previous sessions in the specified directory by reading CLI session storage. Defaults to Claude Code sessions; `--cli codex` filters to Codex only.
- **FR-049**: `/sessions --recent [N]` MUST list the N most recent inactive sessions across all directories, merged by timestamp from both Claude Code and Codex storage. Defaults to 10; any number may be passed. `--cli claude` or `--cli codex` filters to one CLI only.
- **FR-050**: For Claude Code, the system MUST read session metadata from `~/.claude/projects/<encoded-path>/sessions-index.json`, which contains session ID, summary, message count, created/modified timestamps, git branch, and project path. The path encoding replaces `/` with `-` and prepends `-`.
- **FR-051**: For Codex CLI, the system MUST read the first line (`session_meta` record) of each `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` file, which contains thread ID, working directory (`cwd`), timestamp, CLI version, and git info.
- **FR-052**: `/resume <session-id>` MUST spawn a new terminal running the appropriate CLI with a resume flag (`claude -r "<sessionId>"` for Claude Code, `codex resume <threadId>` for Codex), pre-configured to connect to the voice server via MCP.
- **FR-053**: The resumed agent MUST initiate a voice callback to the user after restoring its previous conversation context, so the user can continue hands-free.
- **FR-054**: The system MUST detect which CLI a session belongs to (Claude Code vs Codex) based on the session ID format or storage location, so `/resume` works without requiring the user to specify `--cli`.

**First-Time Setup (Init):**

- **FR-055**: The system MUST provide an `init` CLI command that guides the user through all one-time setup via an interactive wizard with sensible defaults at every step.
- **FR-056**: The init wizard MUST configure: Discord bot token, TTS backend selection (local/cloud), TTS-specific settings (voice profile for local, API key and voice ID for cloud), default CLI client (Claude Code/Codex), speech completion mode default, stop word, Whisper model size, and terminal emulator override (optional).
- **FR-057**: The init wizard MUST offer to install the voice agent as an MCP server in each detected CLI tool's configuration (Claude Code, Codex CLI, or any other detected MCP-compatible client).
- **FR-058**: The init wizard MUST offer to set up a system daemon (systemd unit or equivalent) so the voice server starts on boot and restarts on crash.
- **FR-059**: If the daemon is configured, the wizard MUST offer to start it immediately and confirm it is running.
- **FR-060**: When run on an already-configured system, the init command MUST pre-fill existing values as defaults so the user can update specific settings without re-entering everything.
- **FR-061**: The init command MUST support non-interactive mode via CLI flags for any setting, prompting interactively only for missing required values.
- **FR-062**: The init wizard MUST validate critical inputs (e.g., Discord token validity, directory paths, CLI binary availability) before proceeding and provide clear error messages with remediation guidance.
- **FR-063**: The init wizard MUST write all configuration to a persistent config file on disk and display a summary of what was configured and how to start or use the system.

### Key Entities

- **Speech Completion Mode**: A setting that controls how the system determines when a user's speech turn is complete. Has two variants: "pause" (silence-duration based) and "stop-token" (keyword based).
- **Stop Word Configuration**: The specific word or short phrase that signals end-of-turn in stop-token mode. Includes the word itself and detection parameters (e.g., minimum post-word silence duration).
- **MCP Client Session**: Represents a connected coding agent instance communicating with the server over HTTP/SSE. Each session has a unique identifier, client type (Claude Code, Codex CLI, etc.), assigned voice, session name, and tracks its own call/message state.
- **Voice Pool**: A curated list of TTS voices available for assignment to sessions. Includes default voices that work out of the box and supports user-added voices. Tracks which voices are currently assigned to active sessions.
- **System Voice**: A reserved, distinct TTS voice used exclusively by the switchboard for routing announcements, queue notifications, and navigation prompts. Never assigned to an agent session.
- **Message Queue**: Per-session ordered list of pending messages. For agent→user messages: read out via TTS in the agent's voice. For user→agent cold calls: delivered when the agent next listens. Each entry tracks sender, content, timestamp, and delivery status.
- **Router LLM**: A lightweight language model used to parse short voice command transcripts and determine routing intent. Supports multiple backends (Codex OAuth, OpenRouter, any OpenAI-compatible endpoint). Only active when explicitly enabled.
- **Spawned Session**: Represents a running coding agent CLI process, including its client type, target directory, session name, assigned voice, process handle, terminal PID (if interactive), start time, spawn mode (interactive/headless), owning user, associated MCP client session, cold call delivery adapter (hook-based for Claude Code, JSON-RPC for Codex App Server), and current status (starting, connected, working, idle).
- **Terminal Emulator Configuration**: The user's preferred terminal emulator for interactive spawn mode. Includes the configured override (if any) and the detection chain for finding the system default.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users in stop-token mode can speak for 30+ seconds with natural pauses without being prematurely cut off, and their complete turn is finalized within 2 seconds of saying the stop word.
- **SC-002**: Stop word false-positive rate (premature turn ending when stop word is used naturally mid-sentence) is below 5% in typical conversation.
- **SC-003**: Mode switching takes effect immediately; the next listen operation uses the newly selected mode with no delay or restart required.
- **SC-004**: The server accepts connections from 5+ simultaneous MCP clients without degradation.
- **SC-005**: A spawned coding agent initiates a callback voice call within 30 seconds of the spawn command being issued.
- **SC-006**: Users can list and terminate spawned sessions with a single command each.
- **SC-007**: When one message readout ends and another is queued, the System Voice announcement and transition to the next message completes within 3 seconds.
- **SC-008**: The server runs continuously for 24+ hours without memory leaks or degradation, surviving client connect/disconnect cycles.
- **SC-009**: The router LLM parses and routes a user's voice command within 500ms end-to-end (STT transcript to routing decision).
- **SC-010**: Users can distinguish between concurrent sessions by voice alone (without hearing the session name prefix) at least 80% of the time when using the curated voice pool.
- **SC-011**: Cold call messages are delivered to the target session within 5 seconds of the session's next listen call.

## Assumptions

- The stop word detection operates on transcribed text (post-STT), not on raw audio. The system accumulates multiple speech segments separated by pauses, transcribes each segment, and checks whether the latest segment ends with the stop word. This is simpler and more reliable than real-time keyword spotting in raw audio.
- The spawn feature is client-agnostic. Any MCP-compatible coding agent CLI can be used (Claude Code, Codex CLI, etc.) as long as it supports: (a) connecting to an MCP server via HTTP, and (b) receiving an initial prompt at launch. Both Claude Code and Codex CLI meet these requirements.
- The default CLI client is Claude Code (`claude`). Codex CLI (`codex`) is supported as an alternative. The chosen client must be installed and available on the host machine's PATH.
- The spawned agent connects to the voice agent server via HTTP/SSE transport using a server URL passed at launch time (via `--mcp-config` for Claude Code, or `config.toml` for Codex CLI).
- The MCP protocol's Streamable HTTP transport is used for multi-client support. The server exposes a single HTTP endpoint. Both Claude Code and Codex CLI support this transport natively.
- The stdio transport is retained as a fallback for single-client use cases (backward compatibility), but the HTTP transport is the primary mode for multi-client scenarios.
- Interactive spawn mode (terminal window) is the default. The system detects the terminal emulator via: configured override > `x-terminal-emulator` > `$TERMINAL` env var > scanning PATH for known emulators (gnome-terminal, konsole, xfce4-terminal, kitty, alacritty, wezterm, foot, xterm). If no terminal can be found, the system falls back to headless mode with a warning.
- Discord slash commands are the primary user interface for spawn, session management, and queue management, since the user is already interacting via Discord.
- The default stop word ("over") is chosen for its distinctiveness in conversational English and its existing convention in radio/walkie-talkie communication. Users who find it too common can configure a different word.
- Auto-detection resolves the user's voice channel within the guild where the command was issued. Cross-guild channel detection is out of scope.
- Only one Discord voice channel is active at a time per server instance. Multi-channel support is out of scope.
- The message queue is in-memory. If the server restarts, the queue is lost. Spawned processes persist independently and can be tracked for cleanup on restart.
- The `default_channel_id` configuration remains as a fallback for headless/scripted scenarios but is no longer required for interactive use.
- The curated voice pool ships with reasonable default voices from the existing TTS backend (preset speakers for local Qwen3-TTS, or preset voice IDs for ElevenLabs). Users can customize the pool but are not required to.
- Session names default to the working directory basename (e.g., `/home/joe/Documents/Projects/myproject` → "myproject"), auto-suffixed if a collision exists (e.g., "myproject-2"). Users can override with a custom label at spawn time.
- The router LLM is a lightweight, fast model optimized for short intent classification — not a general-purpose reasoning model. It can be powered by Codex OAuth (reusing `~/.codex/auth.json` JWT credentials to call OpenAI directly), an OpenRouter API key, or any OpenAI-compatible endpoint. The model choice is configurable.
- The System Voice is a distinct TTS voice that is never used for agent sessions. It MUST sound noticeably robotic/neutral — a clearly synthetic, monotone quality that is immediately distinguishable from the natural-sounding curated agent voices.
- Message routing operates at the message level, not the call level. The server manages a continuous voice session with the user; agents send and receive messages through the switchboard rather than making independent "calls."
- Cold call (voicemail) delivery uses a `check_messages` MCP tool exposed by the voice server. The tool returns queued messages as proper tool results (real conversation content). For Claude Code, a `PostToolUse` hook checks the queue and nudges the agent to call `check_messages` only when messages exist (zero overhead otherwise). For Codex CLI, `turn/steer` via App Server provides direct injection, with `check_messages` as a universal fallback. The MCP protocol does not support server-to-client content injection by design, so the tool-based approach is the correct pattern.
- For Codex CLI integration, the voice agent spawns `codex app-server` (not `codex` directly) to gain JSON-RPC control over turns, including `turn/steer` for message injection and `turn/interrupt` for cancellation.
- The HTTP/SSE endpoint binds to localhost only (127.0.0.1). No authentication is required because only local processes can connect. All spawned agents run on the same machine.
- Session names MUST be unique across active sessions. If a directory basename collides with an existing session name, the system auto-suffixes (e.g., "myproject", "myproject-2"). Users can always override with a custom label at spawn time.
- Speech completion mode (pause/stop-token) is a global server-level setting, not per-session. There is one user, one mic, one audio input stream — the listening behavior applies uniformly regardless of which session the reply is destined for.
- Claude Code session metadata is read from `~/.claude/projects/<encoded-path>/sessions-index.json`. The path encoding replaces `/` with `-` and prepends `-` (e.g., `/home/joe/myproject` → `-home-joe-myproject`). Each entry contains `sessionId`, `summary`, `messageCount`, `created`, `modified`, `gitBranch`, and `projectPath`.
- Codex CLI session metadata is read from the first line of `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` files. The first line is a `session_meta` record containing `payload.id` (thread ID), `payload.cwd` (working directory), `payload.timestamp`, and `payload.git` info.
- Session resume uses the CLI's native resume mechanism: `claude -r "<sessionId>"` for Claude Code, `codex resume <threadId>` for Codex CLI. The voice server does not need to reconstruct conversation history — the CLI handles that.
- The system can distinguish Claude Code session IDs (UUID format from sessions-index.json) from Codex thread IDs (UUID format from JSONL metadata) by checking which storage location contains the ID. This allows `/resume` to auto-detect the correct CLI.
- The `init` command is a host-side CLI command (e.g., `voice-agent init`), not a Discord slash command. It runs in the user's terminal before the server is started for the first time.
- The daemon setup uses systemd on Linux. Other platforms are out of scope for the initial implementation but the wizard should degrade gracefully (skip daemon setup with a note).
- MCP server registration for Claude Code writes to `~/.claude/mcp.json` or the project-level `.mcp.json`. For Codex CLI, it writes to the appropriate `config.toml` or equivalent. The wizard detects which CLIs are installed by checking PATH.
- The configuration file written by init stores all settings in a single location (e.g., `~/.config/voice-agent/config.toml` or `.env` in the project directory). The server reads from this file at startup.
