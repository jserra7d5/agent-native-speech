# MCP Tools Reference

## Tool Registration

Tools are defined in `server/main.py` as a list of dicts (`_TOOLS`) matching the MCP Tool schema. They are registered via `_register_handlers()` which sets up `@server.list_tools()` and `@server.call_tool()` decorators on the MCP `Server` instance.

The `handle_call_tool` handler dispatches to `_dispatch()` which routes by tool name. All results are JSON-serialized and returned as `[{"type": "text", "text": ...}]`. Errors (KeyError, ValueError) are caught and returned as text content.

## Tool Signatures

### initiate_call

Joins a Discord voice channel and starts a conversation. Speaks opening message via TTS, listens for reply via STT.

**Parameters:**
- `message` (string, **required**) -- Opening message to speak
- `channel_id` (string, optional) -- Discord voice channel ID. Resolution order: explicit arg > `config.default_channel_id` > auto-detect via `find_user_voice_channel_any()`

**Dispatch logic:**
1. Resolves channel_id (explicit > config default > auto-detect user's voice channel)
2. Auto-registers an AgentSession via `manager.register_session()`
3. Calls `manager.initiate_call(channel_id, message, session_id)`

**Returns:**
```json
{
  "call_id": "uuid-string",
  "transcript": "User's first response text"
}
```

**Error cases:**
- No channel_id and no user in any voice channel: ValueError
- Channel not found / not a voice channel: ValueError from Discord bot

### continue_call

Speaks a message during an active call and listens for the user's reply.

**Parameters:**
- `call_id` (string, **required**) -- Active call session ID from `initiate_call`
- `message` (string, **required**) -- Message to speak

**Returns:**
```json
{
  "transcript": "User's response text"
}
```

### speak_to_user

One-way TTS announcement during an active call. Does not listen for a response.

**Parameters:**
- `call_id` (string, **required**) -- Active call session ID
- `message` (string, **required**) -- Message to speak

**Returns:**
```json
{
  "status": "ok"
}
```

### end_call

Speaks a farewell message, disconnects from voice channel, and cleans up the session.

**Parameters:**
- `call_id` (string, **required**) -- Active call session ID
- `message` (string, **required**) -- Farewell message to speak before disconnecting

**Dispatch logic:**
1. Finds the AgentSession linked to this call_id via `manager._find_session_by_call_id()`
2. Calls `manager.end_call(call_id, message)`
3. Unregisters the AgentSession via `manager.unregister_session()`

**Returns:**
```json
{
  "duration_seconds": 142.5
}
```

### add_correction

Registers an STT word correction. When STT consistently mishears a word, this stores a replacement for automatic future correction.

**Parameters:**
- `wrong` (string, **required**) -- The word as incorrectly transcribed
- `right` (string, **required**) -- The correct replacement word

**Returns:**
```json
{
  "status": "ok",
  "wrong": "joh",
  "right": "Joe"
}
```

### list_corrections

Returns all stored STT word corrections.

**Parameters:** (none)

**Returns:**
```json
{
  "corrections": {
    "joh": "Joe",
    "claude_code": "Claude Code"
  }
}
```

### set_speech_mode

Switches between pause-based and stop-token-based speech completion detection.

**Parameters:**
- `mode` (string, **required**) -- `"pause"` or `"stop_token"`
- `stop_word` (string, optional) -- Keyword for stop_token mode (e.g. "over")

**Returns:**
```json
{
  "mode": "stop_token",
  "stop_word": "over"
}
```

### list_sessions

Lists all active agent sessions connected to the voice server.

**Parameters:** (none)

**Returns:**
```json
{
  "sessions": [
    {
      "session_id": "uuid",
      "session_name": "my-project",
      "client_type": "claude",
      "status": "in_call",
      "voice": "Ryan",
      "started_at": "2026-03-01T12:00:00Z",
      "queued_message_count": 0
    }
  ]
}
```

### check_messages

Checks for queued voice messages from the user (routed via Switchboard). Call when notified of pending messages.

**Parameters:** (none -- session_id is resolved from context or first active session)

**Dispatch logic:**
1. Imports `check_messages` from `server.check_messages`
2. Resolves session_id: from args, or falls back to first active session
3. Calls `_check_messages(manager.switchboard, session_id)`

**Returns:**
```json
{
  "messages": ["Hey, can you also fix the tests?"],
  "count": 1
}
```

## Dispatch Flow

```
handle_call_tool(name, arguments)
  -> _dispatch(name, args, manager, config, speech_mode_manager)
     -> routes to manager.method() or speech_mode_manager.set_mode()
  -> JSON serialize result
  -> return [{"type": "text", "text": json_string}]
```

Error handling in `handle_call_tool`:
- `KeyError` -- returned as text (e.g. invalid call_id)
- `ValueError` -- returned as text (e.g. missing arg, unknown tool)
- Any other `Exception` -- logged with traceback, returned as "Internal error: ..."

## Helper: _require()

```python
def _require(args: dict, key: str) -> Any:
    """Return args[key] or raise ValueError."""
```

Used by every tool dispatcher to validate required arguments before calling SessionManager methods.
