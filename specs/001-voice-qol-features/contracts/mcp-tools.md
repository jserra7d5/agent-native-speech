# MCP Tool Contracts: Voice QoL Features

**Branch**: `001-voice-qol-features` | **Date**: 2026-03-01

## Existing Tools (modified)

### initiate_call

**Change**: `channel_id` becomes fully optional. When omitted AND no `default_channel_id` configured, the server auto-detects the user's current voice channel.

```json
{
  "name": "initiate_call",
  "description": "Join a Discord voice channel and initiate a conversation. Auto-detects the user's voice channel if no channel_id is provided.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "channel_id": {
        "type": "string",
        "description": "Discord voice channel ID. If omitted, auto-detects the user's current channel."
      },
      "message": {
        "type": "string",
        "description": "Opening message to speak to the user."
      },
      "session_name": {
        "type": "string",
        "description": "Optional display name for this session. Defaults to directory basename."
      }
    },
    "required": ["message"]
  }
}
```

**Response**:
```json
{
  "call_id": "uuid-string",
  "session_id": "uuid-string",
  "transcript": "User's first spoken reply"
}
```

### continue_call

**Change**: No schema changes. Behavior change: before listening, checks for queued messages from other sessions and announces them via System Voice if present.

### end_call

**Change**: No schema changes. Behavior change: releases the session's voice assignment and removes it from the active session list.

---

## New Tools

### check_messages

Retrieves any queued voice messages (cold calls/voicemail) for the calling session. Called by agents when nudged by the PostToolUse hook.

```json
{
  "name": "check_messages",
  "description": "Check for queued voice messages from the user. Returns any pending messages that were sent while you were working. Call this when notified of pending voice messages.",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Response** (messages pending):
```json
{
  "messages": [
    {
      "message_id": "uuid-string",
      "from": "user",
      "content": "Hold off on that deploy, I want to review it first.",
      "timestamp": "2026-03-01T15:30:00Z"
    }
  ]
}
```

**Response** (no messages):
```json
{
  "messages": []
}
```

### set_speech_mode

Toggle the global speech completion mode.

```json
{
  "name": "set_speech_mode",
  "description": "Set the speech completion mode. 'pause' uses silence detection, 'stop_token' waits for a spoken keyword.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "mode": {
        "type": "string",
        "enum": ["pause", "stop_token"],
        "description": "Speech completion mode."
      },
      "stop_word": {
        "type": "string",
        "description": "Stop word for stop_token mode. Only used when mode is 'stop_token'."
      }
    },
    "required": ["mode"]
  }
}
```

**Response**:
```json
{
  "mode": "stop_token",
  "stop_word": "over"
}
```

### list_sessions

List active agent sessions.

```json
{
  "name": "list_sessions",
  "description": "List all active agent sessions connected to the voice server.",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Response**:
```json
{
  "sessions": [
    {
      "session_id": "uuid-string",
      "session_name": "myproject",
      "client_type": "claude",
      "directory": "/home/joe/myproject",
      "voice": "Ryan",
      "status": "working",
      "spawn_mode": "interactive",
      "started_at": "2026-03-01T15:00:00Z",
      "has_queued_messages": true,
      "queued_message_count": 2
    }
  ]
}
```
