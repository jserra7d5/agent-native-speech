# Init Wizard & Service Setup Reference

## CLI Entry Point

`server/init/__init__.py` provides the `voice-agent` CLI with two subcommands:

```
voice-agent init    # Run setup wizard (default if no subcommand)
voice-agent serve   # Start the voice agent server
```

Also runnable as `python -m server.init`.

### init subcommand flags

| Flag | Description |
|---|---|
| `--discord-token` | Discord bot token |
| `--tts-backend` | `local` or `elevenlabs` |
| `--tts-voice` | Default voice name |
| `--elevenlabs-api-key` | ElevenLabs API key |
| `--elevenlabs-voice-id` | ElevenLabs voice ID |
| `--whisper-model` | Whisper model size |
| `--speech-mode` | `pause` or `stop_token` |
| `--stop-word` | Stop word for stop_token mode |
| `--default-cli` | `claude` or `codex` |
| `--terminal` | Terminal emulator override |
| `--server-host` | Server bind host (default: 127.0.0.1) |
| `--server-port` | Server bind port (default: 8765) |
| `--skip-mcp` | Skip MCP registration step |
| `--skip-daemon` | Skip systemd service installation |
| `--non-interactive` | Non-interactive mode (use flags and defaults) |

## Wizard Flow

`server/init/wizard.py` -- `run_wizard(args)` runs a 10-step interactive wizard.

### Pre-fill Behavior

On re-run, the wizard loads existing config from:
1. `~/.config/voice-agent/config.json` (preferred)
2. `~/.config/voice-agent/config.env` (legacy, parsed and converted via `_env_to_nested_dict()`)

Existing values are used as defaults in prompts, so the user can press Enter to keep them.

### Steps

**Step 1: Discord Bot Token** -- Required. In non-interactive mode, must exist in existing config or be provided via `--discord-token`.

**Step 2: TTS Backend** -- Choice: `local` (Qwen3-TTS) or `elevenlabs` (cloud). If elevenlabs:
- Prompts for ElevenLabs API key
- Interactive voice alias setup: enter `name=voiceId` pairs
- Auto-sets `default_voice_id` to first voice if not already set

**Step 3: Default Voice** -- Presents available voices: Ryan, Aiden, Vivian, Serena, Dylan, Eric.

**Step 4: STT Backend** -- Choice: `local` (Whisper) or `elevenlabs` (Scribe). Prompts for ElevenLabs API key if needed and not already provided.

**Step 5: Default CLI** -- Choice: `claude` or `codex`.

**Step 6: Speech Completion Mode** -- Choice: `pause` (silence detection) or `stop_token` (keyword). If stop_token, prompts for the stop word (default: "over").

**Step 7: Whisper Model** -- Choices: tiny, base, small, medium, large-v3. Larger = more accurate but slower.

**Step 8: Terminal Emulator** -- Auto-detects from: ghostty, kitty, alacritty, wezterm, gnome-terminal, konsole, xfce4-terminal, xterm. User can override or accept detected.

**Step 9: Server Host & Port** -- Defaults: 127.0.0.1:8765.

**Step 10: Review** -- Carries over hardware defaults (device: cuda) and existing LLM/router config from previous runs.

### Config Output

`write_config(config)` writes JSON to `~/.config/voice-agent/config.json`, creating the directory if needed. Returns the Path.

## Post-Wizard Steps

After the wizard completes, `_run_init()` in `server/init/__init__.py` runs:

### MCP Registration

`server/init/mcp_register.py` -- `register_all(server_url, interactive)`:

1. **Detects CLIs**: Checks PATH for `claude` and `codex` binaries via `shutil.which()`
2. **Registers in each detected CLI**:

**Claude Code registration** (`register_claude()`):
- Primary: `claude mcp add --transport http --scope user voice-agent <url>`
- Fallback: Writes directly to `~/.claude.json`:
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
- Updates existing entries if already registered (preserves other entries)

**Codex registration** (`register_codex()`):
- Writes to `~/.codex/config.toml`:
  ```toml
  [mcp_servers.voice-agent]
  type = "http"
  url = "http://127.0.0.1:8765/mcp"
  ```
- Replaces existing `[mcp_servers.voice-agent]` section if present
- Creates file/directory if needed

In interactive mode, prompts for confirmation before each registration.

### Systemd Service

`server/init/systemd.py` -- only runs if systemd is available (`/run/systemd/system` exists):

**Unit template:**
```ini
[Unit]
Description=Voice Agent MCP Server (Discord)
After=network.target

[Service]
Type=simple
WorkingDirectory={project_dir}
ExecStart={venv_python} -m server.main --transport http --config {config_path}
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**Variables interpolated:**
- `{project_dir}` -- the agent-native-speech project directory (derived from init module location)
- `{venv_python}` -- `sys.executable` (the venv Python binary)
- `{config_path}` -- path returned by `write_config()` (usually `~/.config/voice-agent/config.json`)

**Install path:** `~/.config/systemd/user/voice-agent.service`

**Service name:** `voice-agent.service`

**Functions:**
- `is_systemd_available()` -- checks for `/run/systemd/system`
- `install_service(project_dir, config_path)` -- writes the unit file, returns Path
- `enable_and_start()` -- runs `systemctl --user daemon-reload`, `enable`, `start`
- `check_status()` -- runs `systemctl --user is-active voice-agent.service`

In interactive mode, prompts before installing and before starting the service.

## Non-Interactive Mode

With `--non-interactive`, the wizard:
- Uses CLI flags for all values
- Falls back to existing config values
- Falls back to defaults
- Requires `--discord-token` if no existing config has one
- Requires `--elevenlabs-api-key` if using elevenlabs backend without existing key
- Auto-installs systemd service (unless `--skip-daemon`)
- Auto-registers MCP servers (unless `--skip-mcp`)

Example fully automated setup:
```bash
python -m server.init init \
  --discord-token "TOKEN" \
  --tts-backend elevenlabs \
  --elevenlabs-api-key "KEY" \
  --default-cli claude \
  --non-interactive
```

## Helper Functions

### _prompt(question, default, choices)

Interactive text prompt. If `choices` are provided, displays numbered list. Accepts both the number and the text value. Returns `default` on empty input.

### _prompt_bool(question, default)

Yes/no prompt. Shows `[Y/n]` or `[y/N]` based on default. Returns bool.

### _detect_terminal()

Scans PATH for known terminal emulators in order: ghostty, kitty, alacritty, wezterm, gnome-terminal, konsole, xfce4-terminal, xterm. Returns first found or empty string.

### _env_to_nested_dict(env_config)

Converts a flat env-var dict (from parsing a .env file) into the nested JSON config structure. Used when loading existing legacy config for pre-filling wizard defaults.
