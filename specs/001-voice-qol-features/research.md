# Research: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01

## R1: MCP Streamable HTTP Transport

**Decision**: Use `StreamableHTTPSessionManager` from `mcp.server.streamable_http_manager` with Starlette + uvicorn for multi-client HTTP transport.

**Rationale**: The MCP Python SDK (>=1.0) has built-in Streamable HTTP support. `StreamableHTTPSessionManager` handles session tracking, resumability, and multi-client connection management. The deprecated SSE transport (`mcp.server.sse`) still works but Streamable HTTP is the official replacement as of MCP spec 2025-03-26. Starlette is already a transitive dependency of the mcp package.

**Migration path**:
- Current: `mcp.server.stdio.stdio_server()` → single client, lifecycle tied to parent
- New: `StreamableHTTPSessionManager` wrapping a `Server` instance, mounted as a Starlette route
- The manager creates a per-request `StreamableHTTPServerTransport`, handles `Mcp-Session-Id` headers, and supports both stateful and stateless modes
- Stateful mode (recommended): tracks sessions, supports resumability via `Last-Event-ID`
- Single endpoint: both POST (requests/notifications) and GET (SSE stream for server-initiated messages) on the same path

**Integration pattern**:
```python
from starlette.applications import Starlette
from starlette.routing import Route
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

manager = StreamableHTTPSessionManager(server, json_response=True, stateless=False)
app = Starlette(routes=[Route("/mcp", endpoint=manager.handle_request, methods=["GET", "POST"])])
# Run with: uvicorn app:app --host 127.0.0.1 --port 8765
```

**Backward compatibility**: Retain stdio transport as a startup flag (`--transport stdio`) for single-client use. HTTP is the default for the persistent server mode.

**Alternatives considered**:
- FastMCP high-level wrapper — simpler but less control over server lifecycle and tool registration; our existing `Server` + decorator pattern works well
- Raw `StreamableHTTPServerTransport` — too low-level, requires manual session management

---

## R2: Codex App-Server JSON-RPC Protocol

**Decision**: Spawn `codex app-server --listen stdio://` as a subprocess and communicate via JSONL on stdin/stdout.

**Rationale**: The Codex app-server exposes full JSON-RPC control over threads and turns. Key methods:
- `initialize` + `initialized` — required handshake
- `thread/start` — create a new thread (session)
- `thread/resume` — resume an existing thread by ID
- `turn/start` — send user input, agent begins working
- `turn/steer` — inject additional user input mid-turn (cold call delivery)
- `turn/interrupt` — cancel active turn
- Notifications: `turn/started`, `turn/completed`, `item/started`, `item/agentMessage/delta`, `item/completed`

**Cold call delivery**: Use `turn/steer` with `params.input[].type="text"` to inject voicemail messages into active turns. The agent adapts in real-time without starting a new turn. Fallback: `check_messages` MCP tool if `turn/steer` fails.

**MCP configuration for Codex**: Configured via `~/.codex/config.toml`:
```toml
[mcp_servers.voice-agent]
enabled = true
command = "curl"  # or HTTP type when supported
```
For HTTP MCP servers, Codex supports `type = "http"` in config.toml.

**Session resume**: `thread/resume` with `params.threadId` reopens a previous conversation thread. Combined with `turn/start`, the resumed agent can immediately call the voice server.

**Alternatives considered**:
- Spawning `codex` directly (interactive CLI) — no programmatic control over turns, can't inject messages mid-run
- WebSocket transport (`--listen ws://...`) — experimental/unsupported

---

## R3: Claude Code MCP Configuration

**Decision**: Register the voice agent as an HTTP MCP server in Claude Code's configuration using the `claude mcp add` CLI or by writing to `~/.claude.json` (user scope) or `.mcp.json` (project scope).

**Rationale**: Claude Code supports three MCP config scopes:
- **Project scope**: `.mcp.json` in project root (shared via git)
- **User scope**: `~/.claude.json` (personal, cross-project)
- **Local scope**: `~/.claude.json` under project paths (private per-project)

**HTTP server registration format**:
```json
{
  "mcpServers": {
    "voice-agent": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

**Init wizard integration**: The wizard will use `claude mcp add --transport http --scope user voice-agent http://127.0.0.1:8765/mcp` to register globally, or write directly to `~/.claude.json` if the CLI is not available.

**PostToolUse hook for cold calls**: Create a hook script that checks a message queue file/socket. Install as a Claude Code hook via `.claude/hooks.json`:
```json
{
  "hooks": {
    "PostToolUse": [{
      "command": "/path/to/check_voice_queue.sh",
      "timeout": 500
    }]
  }
}
```
The hook returns `additionalContext` instructing the agent to call `check_messages` when queued messages exist.

**`--mcp-config` flag**: When spawning Claude Code for a session, pass `--mcp-config /path/to/mcp.json` to ensure the voice agent MCP server is available. Combined with `--strict-mcp-config` for isolated setups.

**Alternatives considered**:
- Managed MCP configs (`/etc/claude-code/mcp.json`) — requires root, overkill for single-user
- Project-scoped `.mcp.json` only — not portable across projects for the voice agent

---

## R4: Stop-Token Speech Detection

**Decision**: Implement stop-token detection at the segment level in the STT pipeline. Accumulate multiple speech segments (separated by natural pauses) and check whether the last segment ends with the stop word. Finalize only when the stop word is detected or max timeout is reached.

**Rationale**: The current `listen()` method uses VAD silence detection to determine end-of-turn. In stop-token mode, the VAD still detects individual speech segments (each pause triggers a segment boundary), but instead of returning immediately, the pipeline accumulates segments and transcribes each one to check for the stop word.

**Detection algorithm**:
1. VAD emits `SpeechEvent(type="end")` with audio for each pause-delimited segment
2. Transcribe the segment with Whisper
3. Check if the transcript ends with the stop word (case-insensitive, after stripping punctuation)
4. If stop word found: concatenate all accumulated segment transcripts, strip the trailing stop word, return
5. If not found: save the transcript, reset VAD, continue listening for the next segment
6. Safety fallback: max timeout (60s default) forces return regardless

**Mid-sentence false positive prevention**: The stop word must appear at the END of a segment (after a VAD-detected pause), not mid-speech. If the user says "I went over the document" without pausing after "over", the VAD doesn't trigger, so "over" is never checked as a stop word. This naturally handles FR-006.

**Alternatives considered**:
- Real-time keyword spotting on raw audio — more complex, requires a separate model, overkill when post-STT detection works reliably
- Full-transcript regex matching — would miss the pause context needed to distinguish mid-sentence usage from turn-ending usage

---

## R5: Terminal Emulator Spawning

**Decision**: Use a detection chain (configured override → `x-terminal-emulator` → `$TERMINAL` → known emulator scan) to find a terminal emulator, then launch it with `-e` flag to execute the agent CLI.

**Rationale**: Linux has no single standard for terminal emulator detection. The detection chain is:
1. User-configured override in config (e.g., `TERMINAL_EMULATOR=wezterm`)
2. `x-terminal-emulator` symlink (Debian/Ubuntu alternatives system)
3. `$TERMINAL` environment variable
4. Scan PATH for known emulators: gnome-terminal, konsole, xfce4-terminal, kitty, alacritty, wezterm, foot, xterm

**Launch patterns** (vary by emulator):
- Generic: `<emulator> -e <command>` (works for most)
- gnome-terminal: `gnome-terminal -- <command>`
- kitty: `kitty <command>` (no `-e` needed)
- wezterm: `wezterm start -- <command>`
- tmux (if in tmux session): `tmux new-window <command>`

**Implementation**: `subprocess.Popen([emulator, *flags, "--", command], cwd=target_dir)` with non-blocking spawn. Track the PID for session management (kill on session end).

**Headless fallback**: If no terminal found or `--headless` flag, use `subprocess.Popen(command, cwd=target_dir)` without a terminal wrapper.

**Alternatives considered**:
- Electron/web-based terminal — overkill, external dependency
- SSH-based remote terminal — out of scope for local-only system

---

## R6: LLM Router for Voice Command Intent Classification

**Decision**: Use a lightweight cloud LLM call with structured output for intent classification. Support Codex OAuth, OpenRouter, or any OpenAI-compatible endpoint. No local model required.

**Rationale**: The router needs to classify short voice transcripts (5-50 words) into 4 intents:
- `reply_current` — implicit reply to last speaker ("yes, go ahead")
- `route_to_session` — explicit routing ("tell api-server to hold off")
- `cold_call` — voicemail to busy session ("send myproject a message: ...")
- `navigation` — queue navigation ("next", "skip", "list sessions")

A cloud LLM call with structured output (tool-calling or JSON mode) provides the best accuracy for ambiguous voice transcripts while staying under 500ms. The model receives the transcript plus a list of active session names and returns a structured intent classification.

**Prompt pattern**: System prompt lists active sessions and defines the 4 intents. User message is the transcript. Output is a tool call with `intent`, `target_session` (optional), and `message_content` (optional).

**Fuzzy session name matching**: Use `thefuzz` library with `token_sort_ratio` scorer (handles word reordering like "my project" → "myproject") as a pre-processing step before sending to the LLM. Threshold: 75+ for a match.

**Backend options**:
- Codex OAuth: Reuse `~/.codex/auth.json` JWT credentials to call OpenAI's chat completions API directly. No additional API key needed.
- OpenRouter: API key-based, model-agnostic
- Any OpenAI-compatible endpoint: Local or remote

**Fallback when disabled**: Simple FIFO ordering, all replies go to last speaker. Zero LLM overhead.

**Alternatives considered**:
- Local embedding + cosine similarity — fast (<1ms) but poor accuracy for ambiguous intents and session name extraction
- Local tiny LLM (Qwen3-0.6B via Ollama) — adds local model dependency and GPU contention with TTS/STT; latency similar to cloud call
- Fine-tuned BERT classifier — requires training data, can't extract session names or message content

---

## R7: Systemd Daemon Setup

**Decision**: Generate a systemd user unit file at `~/.config/systemd/user/voice-agent.service` and enable/start it via `systemctl --user` commands.

**Rationale**: systemd user services provide auto-start on login, crash recovery (`Restart=always`), and standard log management (`journalctl --user`). User-level units don't require root permissions.

**Unit file template**:
```ini
[Unit]
Description=Voice Agent MCP Server (Discord)
After=network.target

[Service]
Type=simple
WorkingDirectory={project_dir}
ExecStart={venv_python} -m server.main --transport http
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
Environment=DOTENV_PATH={config_path}

[Install]
WantedBy=default.target
```

**Programmatic setup** (from init wizard):
1. Check `os.path.exists('/run/systemd/system')` for systemd availability
2. Write unit file to `~/.config/systemd/user/voice-agent.service`
3. Run `systemctl --user daemon-reload`
4. Run `systemctl --user enable voice-agent.service`
5. Optionally: `systemctl --user start voice-agent.service`

**Non-systemd fallback**: Skip daemon setup with a note. User can run the server manually.

**Alternatives considered**:
- System-level systemd service — requires root, inappropriate for single-user desktop
- Docker container — adds complexity, harder to access Discord bot token and GPU
- Supervisor/pm2 — extra dependency when systemd is already available on Linux

---

## R8: Init CLI Wizard

**Decision**: Implement as a `voice-agent init` entry point using Python's built-in `input()` for interactive prompting, with CLI flags for non-interactive mode via `argparse`.

**Rationale**: The init command configures all settings, registers MCP servers in CLI tools, and optionally sets up the systemd daemon. It writes to a persistent config file that the server reads at startup.

**Config file location**: `~/.config/voice-agent/config.env` (dotenv format for compatibility with the existing `Config.from_env()` loader). The init wizard writes this file, and the server's `Config.from_env()` is extended to load from this path as a fallback when no `.env` exists in the working directory.

**Setup steps** (interactive wizard flow):
1. Discord bot token (with brief setup instructions)
2. TTS backend selection (local/elevenlabs)
3. Backend-specific settings (voice profile for local, API key + voice ID for ElevenLabs)
4. Default CLI client (claude/codex)
5. Speech completion mode default (pause/stop-token)
6. Stop word (default: "over")
7. Whisper model size (tiny/base/small/medium/large-v3)
8. Terminal emulator override (optional)
9. MCP server registration in detected CLI tools
10. Daemon setup (systemd)
11. Summary + next steps

**Re-run behavior**: Read existing config, pre-fill as defaults, let user change specific values.

**Alternatives considered**:
- TUI framework (textual, rich) — adds dependency, overkill for a one-time wizard
- Web-based setup — requires browser, adds complexity
- YAML/TOML config format — less compatible with existing `.env`-based Config loader
