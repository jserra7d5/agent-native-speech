# Discord Bot Reference

## Architecture

The Discord bot (`server/discord_bot.py`) consists of two classes:

- **`VoiceBot`** (`commands.Bot` subclass) -- the actual Discord bot with slash commands and voice management
- **`BotRunner`** -- manages running VoiceBot in a background daemon thread

The bot runs in its own thread with its own asyncio event loop, separate from the MCP server's main event loop. All cross-thread communication goes through `BotRunner.run_coroutine()`.

## BotRunner API

### Constructor

```python
BotRunner(config: Config)
```

Creates a `VoiceBot` instance and stores the config. Does not start the thread yet.

### start()

```python
def start(self) -> None:
```

Spawns a daemon thread named `"discord-bot"` that:
1. Creates a new asyncio event loop
2. Sets it as the thread-local event loop
3. Runs `bot.start(config.discord_token)` until completion

### run_coroutine(coro) -> result

```python
def run_coroutine(self, coro) -> Any:
```

**Blocking call.** Schedules an async coroutine on the bot's event loop from any thread. Uses `asyncio.run_coroutine_threadsafe()`. Waits up to **30 seconds** for the result. This is the primary bridge between the MCP server thread and the Discord bot thread.

Usage from MCP dispatch:
```python
detected = manager._runner.run_coroutine(
    manager._runner.bot.find_user_voice_channel_any()
)
```

### run_coroutine_async(coro) -> Future

```python
def run_coroutine_async(self, coro) -> concurrent.futures.Future:
```

**Non-blocking.** Returns a `Future` that can be checked later. Used when the caller does not need to wait for the result immediately.

### shutdown()

```python
def shutdown(self) -> None:
```

Schedules `bot.close()` on the bot's event loop, waits up to 10 seconds, then joins the thread with a 5-second timeout.

## VoiceBot

### Intents

```python
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
```

### Component Wiring

Components are injected after construction via setter methods. All are optional at construction time (None until set):

```python
bot.set_correction_manager(manager)    # CorrectionManager for /correct, /corrections
bot.set_speech_mode_manager(manager)   # SpeechModeManager for /mode, /stopword
bot.set_spawn_manager(manager)         # SpawnManager for /spawn, /kill, /resume
bot.set_session_manager(manager)       # SessionManager for /sessions, /kill, /spawn, /resume
bot.set_session_browser(browser)       # SessionBrowser for /sessions history browsing
```

Each slash command checks if its required manager is None and returns an ephemeral "not available yet" message if so.

### Voice Channel Methods

```python
async def join_voice_channel(channel_id: int) -> discord.VoiceClient
```
Joins a voice channel. Uses `voice_recv.VoiceRecvClient` if discord-ext-voice-recv is installed, falls back to standard `VoiceClient`. Returns existing connection if already connected to the channel.

```python
async def leave_voice_channel(channel_id: int) -> None
```
Disconnects from a voice channel by ID.

```python
async def find_user_voice_channel_any() -> int | None
```
Scans all guilds and voice channels to find the first channel with a non-bot user. Returns channel ID or None. Used for auto-detection when no channel_id is provided to `initiate_call`.

### Voice State Handling

`on_voice_state_update` handles:
1. **Bot disconnected** (kicked/channel deleted) -- triggers `_on_user_leave` callback for cleanup
2. **User left channel** -- if the channel is now empty (only bots), auto-disconnects and triggers cleanup
3. **User moved channels** -- same empty-channel check on the old channel

The `_on_user_leave` callback is set by `CallManager` to clean up orphaned call sessions.

### on_ready

Syncs slash commands with Discord via `self.tree.sync()` and sets the `_ready_event` so that `wait_until_bot_ready()` unblocks.

## Slash Commands

All slash commands are registered in `_register_slash_commands()` during `VoiceBot.__init__()`. All responses are **ephemeral** (only visible to the command invoker).

### /correct

```
/correct wrong:<string> right:<string>
```

Adds an STT correction for the invoking user. Calls `self._correction_manager.add_correction(user_id, wrong, right)`.

### /corrections

```
/corrections
```

Lists all STT corrections for the invoking user. Displays as bullet list: `- "wrong" -> "right"`.

### /mode

```
/mode mode:<pause|stop_token> [stop_word:<string>]
```

Sets the speech completion mode. `mode` is a Choice parameter with dropdown. Calls `self._speech_mode_manager.set_mode(mode, stop_word=stop_word)`.

### /stopword

```
/stopword word:<string>
```

Changes the stop word without changing the mode. Preserves the current mode and only updates the stop word.

### /spawn

```
/spawn directory:<string> [cli:<string>] [voice:<string>] [headless:<bool>]
```

Launches a coding agent CLI session:
1. Calls `self._spawn_manager.spawn_session(directory, cli, voice, headless, user_id)`
2. Registers the new session via `self._session_manager.register_session()`
3. Reports the session name and assigned voice

The spawned agent will automatically call `initiate_call` to connect to the user's voice channel.

### /sessions

```
/sessions [directory:<string>] [recent:<int>] [cli:<string>]
```

Two modes:
- **With directory/recent args**: Uses `SessionBrowser` to browse session history (Claude/Codex session logs)
- **Without args**: Lists active sessions from `SessionManager` with relative timestamps and queued message counts

### /kill

```
/kill session:<string>
```

Terminates an active session by name or ID:
1. Finds the session in active sessions list
2. Gets the full session object for PIDs
3. Calls `self._spawn_manager.kill_session(process_pid, terminal_pid)` to send SIGTERM
4. Unregisters the session from SessionManager

### /resume

```
/resume session_id:<string> [voice:<string>] [headless:<bool>]
```

Resumes a previous CLI session:
1. Looks up session via `SessionBrowser.find_session()` to get CLI type and working directory
2. Falls back to `SpawnManager.default_cli` if session not found
3. Spawns via `SpawnManager.spawn_session(resume_session_id=...)` which passes `--resume` (Claude) or `resume` subcommand (Codex)
4. Registers the new session with the resolved directory and voice

## DAVE E2EE Patches

Two monkey-patches are applied at module import time (before any bot instance is created):

### _patch_voice_recv_router()

Wraps `PacketRouter._do_run` to catch `OpusError` on individual packets instead of crashing the entire audio receive thread. Without this patch, a single corrupted Opus packet (common after TTS playback ends) kills audio reception.

### _patch_voice_recv_dave_decrypt()

Replaces `AudioReader.callback` with a DAVE-aware version. Discord mandates DAVE E2EE which adds a second encryption layer on Opus payloads (on top of transport-layer encryption). The patch:

1. Performs standard transport-layer decryption (aead_xchacha20_poly1305_rtpsize)
2. Checks if DAVE session exists and is ready
3. If the user is not in passthrough mode, decrypts the DAVE layer via `conn.dave_session.decrypt(user_id, davey.MediaType.audio, data)`
4. Handles passthrough mode (no DAVE decryption needed) and not-ready state (pass through packet)
5. Logs DAVE state periodically for debugging (first 3 packets + every 200th)

Required imports: `discord.ext.voice_recv`, `davey` (DAVE library).

## Thread Model Diagram

```
Main Thread (MCP Server)              Background Thread (Discord Bot)
========================              ==============================
asyncio event loop                    asyncio event loop (new_event_loop)
  |                                     |
  | MCP tool call                       | bot.start(token)
  |   -> _dispatch()                    |   -> on_ready() -> tree.sync()
  |     -> manager.method()             |   -> on_voice_state_update()
  |       -> runner.run_coroutine()  ---|---> coroutine runs here
  |          (blocks, 30s timeout)      |
  |       <- result                     |
  |                                     |
  | Signal handler                      |
  |   -> shutdown_event.set()           |
  |   -> runner.shutdown()           ---|---> bot.close()
```
