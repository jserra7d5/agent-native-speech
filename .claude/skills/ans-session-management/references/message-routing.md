# Message Routing

## Switchboard (`server/switchboard.py`)

Per-session message queuing and delivery for multi-agent scenarios.

### Data Structures

**QueuedMessage** dataclass:
```python
@dataclass
class QueuedMessage:
    message_id: str      # UUID4, auto-generated
    direction: str       # "agent_to_user" or "user_to_agent"
    sender_session: str | None  # Session ID of sender (None if from user)
    content: str         # Message text
    timestamp: float     # Unix timestamp, auto-generated
    delivered: bool      # Whether the message has been read out
```

**MessageQueue** -- per-session ordered list with max depth enforcement:
```python
class MessageQueue:
    session_id: str
    max_depth: int              # Default 20, from config.max_queue_depth
    _messages: list[QueuedMessage]
```

Key behaviors:
- `enqueue()` returns `False` and logs warning if queue is full (max depth reached).
- `get_pending()` returns undelivered messages, optionally filtered by direction.
- `deliver_next()` marks and returns the next undelivered message.
- `mark_all_delivered()` bulk-marks all pending messages.
- `drain()` removes and returns all messages (used during unregister).

### Switchboard Class

**Internal state:**
```python
_max_queue_depth: int                    # Per-queue message limit
_queues: dict[str, MessageQueue]         # session_id -> MessageQueue
_last_speaker_session: str | None        # Session that last spoke to user
_session_names: dict[str, str]           # session_id -> display name
```

### Session Lifecycle in Switchboard

- `register_session(session_id, session_name)` -- Creates a MessageQueue. Called by SessionManager during registration.
- `unregister_session(session_id)` -- Drains the queue, removes name mapping, clears last_speaker if it was this session. Returns remaining messages.
- `update_session_name(session_id, name)` -- Updates display name for message prefixing.

### Message Directions

**agent_to_user** -- Agent wants to talk to the user but cannot (another session has the voice channel):
```python
switchboard.enqueue_agent_message(session_id, "I found the bug, ready to discuss")
```
These are announced by System Voice via `get_pending_announcements()`.

**user_to_agent** -- User sends a cold-call message to a busy agent:
```python
switchboard.enqueue_user_message(session_id, "Hey, pause what you're doing")
```
These are retrieved by the agent via the `check_messages` MCP tool.

### Announcement Flow

`get_pending_announcements(session_count)` returns formatted announcement dicts:
```python
[{
    "session_id": "uuid-...",
    "session_name": "myproject",
    "content": "myproject: I found the bug, ready to discuss",  # prefixed when session_count > 1
    "message_id": "uuid-...",
    "timestamp": 1709312345.0,
}]
```

When `session_count > 1`, content is prefixed with `"{session_name}: "` so the user hears which agent is speaking. When only one session, no prefix.

Note: `get_pending_announcements()` does NOT mark messages as delivered. The caller must explicitly call `mark_messages_delivered()` after the System Voice has spoken them.

### Routing State

`last_speaker_session` tracks which session most recently spoke to the user. Used by the router as context for the `reply_current` intent:
- `set_last_speaker(session_id)` -- called after a session speaks to the user.
- `last_speaker_session` property -- read by the router to know the default target.

### Query Helpers

- `has_pending_for_session(session_id)` -- bool, used by `list_active_sessions()`.
- `pending_count_for_session(session_id)` -- int, used by `list_active_sessions()`.
- `get_queue_info()` -- returns `{session_id: {"pending": N, "total": M}}` for all queues.

## IntentRouter (`server/router.py`)

LLM-powered classification of user speech to determine routing in multi-session scenarios.

### RouterIntent Dataclass

```python
@dataclass
class RouterIntent:
    intent: str = "reply_current"          # One of the four intents
    target_session: str | None = None      # For route_to_session, cold_call
    message_content: str | None = None     # For cold_call (the message to deliver)
    navigation_action: str | None = None   # For navigation (next/skip/list)
    confidence: float = 1.0                # 0.0-1.0
```

### Four Intents

| Intent | When | Fields Set |
|---|---|---|
| `reply_current` | User responds to the session that just spoke. Default. | (none extra) |
| `route_to_session` | User addresses a specific session by name. "Hey myproject, fix that." | `target_session` |
| `cold_call` | User sends async message to a non-active session. "Tell backend to hold off." | `target_session`, `message_content` |
| `navigation` | User controls the switchboard. "Next", "skip", "list sessions." | `navigation_action` |

### Classification Flow

`IntentRouter.classify(transcript, active_sessions)`:

1. If `router.enabled` is false, return `reply_current` immediately.
2. If only one active session, return `reply_current` (no routing needed).
3. Call `_classify_via_llm()`:
   a. Build system prompt with active session names and client types.
   b. Send to LLM (temperature=0, max_tokens=200).
   c. Parse JSON response.
   d. Fuzzy-match any `target_session` against active session names.
4. On any error: return `reply_current` with `confidence=0.5`.

### LLM Backend Configuration

Uses shared `LLMConfig` (same as correction.py):
- `llm.backend`: "openrouter", "codex_oauth", or "openai_compatible"
- `llm.model`: Default model (router can override with `router.model`)
- `llm.api_key`: API key for openrouter/openai_compatible
- `llm.api_base_url`: Custom endpoint URL
- `llm.codex_auth_path`: JWT token file path for codex_oauth backend
- `router.timeout_ms`: Router-specific timeout (falls back to `llm.timeout_ms`)

Backend URL defaults:
```python
"openrouter": "https://openrouter.ai/api/v1"
"codex_oauth": "https://api.openai.com/v1"
"openai_compatible": ""  # must be configured
```

### Fuzzy Session Name Matching

`_resolve_session_name(raw_name, active_sessions)`:
- Uses `thefuzz.process.extractOne` with `fuzz.token_sort_ratio` scorer.
- Score cutoff: 75 (`_FUZZY_THRESHOLD`).
- Returns matched session name or None if below threshold.
- Handles pronunciation variations and abbreviations from speech-to-text.

When fuzzy match fails:
- `route_to_session` and `cold_call` intents fall back to `reply_current` with `confidence=0.4`.

### Response Parsing

`_parse_response()` handles:
- Markdown code fence stripping (LLMs sometimes wrap JSON in triple backticks).
- JSON parse failure: returns `reply_current` with `confidence=0.3`.
- Unknown intent values: falls back to `reply_current`.
- Unknown `navigation_action`: falls back to `reply_current`.
- Valid navigation actions: `"next"`, `"skip"`, `"list"`.

### HTTP Client

Uses a reusable `httpx.AsyncClient` (lazy-initialized, recreated if closed). Call `close()` to clean up.

## check_messages Tool (`server/check_messages.py`)

Synchronous function called as an MCP tool. Returns pending user-to-agent messages.

```python
def check_messages(switchboard: Switchboard, session_id: str) -> dict[str, Any]:
```

Flow:
1. Call `switchboard.get_pending_user_messages(session_id)` to get undelivered messages.
2. Mark each message as `delivered = True` directly on the dataclass.
3. Format into response dict:
```python
{
    "messages": [
        {
            "message_id": "uuid-...",
            "from": "user",
            "content": "Hey, pause what you're doing",
            "timestamp": "2024-03-01T12:00:00Z"
        }
    ]
}
```

If no messages are pending, returns `{"messages": []}`.

## Multi-Session Message Flow (End to End)

### Scenario: User sends cold-call to busy agent

1. User says: "Tell myproject to pause the deploy"
2. STT transcribes the speech.
3. `IntentRouter.classify()` returns `RouterIntent(intent="cold_call", target_session="myproject", message_content="pause the deploy")`.
4. System looks up "myproject" session ID from `SessionManager`.
5. `Switchboard.enqueue_user_message(session_id, "pause the deploy")` queues the message.
6. Later, the "myproject" agent calls the `check_messages` MCP tool.
7. `check_messages()` retrieves the pending message, marks delivered, returns it.
8. Agent reads: `{"messages": [{"from": "user", "content": "pause the deploy", ...}]}`.

### Scenario: Agent queues announcement while another session is active

1. "backend-api" agent finishes a task, calls `speak_to_user`.
2. But "frontend" session currently has the voice channel.
3. System calls `Switchboard.enqueue_agent_message(backend_session_id, "Deploy complete")`.
4. When "frontend" session finishes its turn, system calls `get_pending_announcements(session_count=2)`.
5. Returns: `[{"content": "backend-api: Deploy complete", ...}]` (prefixed because multi-session).
6. System Voice speaks the announcement using `VoicePool.get_system_voice()`.
7. System calls `mark_messages_delivered(backend_session_id, direction="agent_to_user")`.

## Session Browser (`server/session_browser.py`)

Reads previous session metadata from the filesystem. Used by the `/sessions` and `/resume` Discord commands.

### SessionMetadata Dataclass

```python
@dataclass
class SessionMetadata:
    session_id: str        # Session identifier
    cli: str               # "claude" or "codex"
    summary: str           # Session summary or first prompt
    directory: str         # Project directory path
    timestamp: float       # Unix timestamp (last modified)
    message_count: int = 0 # Number of messages (Claude only)
    git_branch: str = ""   # Git branch (if available)
```

### Claude Code Sessions

Path: `~/.claude/projects/<encoded-path>/sessions-index.json`

Path encoding: directory path with `/` replaced by `-`. Example:
- `/home/joe/myproject` -> `-home-joe-myproject`

Index file structure:
```json
{
    "entries": [
        {
            "sessionId": "uuid-...",
            "summary": "Working on the auth module",
            "firstPrompt": "Help me fix the login bug",
            "projectPath": "/home/joe/myproject",
            "created": "2024-03-01T10:00:00Z",
            "modified": "2024-03-01T12:00:00Z",
            "messageCount": 42,
            "gitBranch": "feature/auth"
        }
    ]
}
```

`summary` is preferred over `firstPrompt` for display. `modified` is preferred over `created` for sorting.

### Codex Sessions

Path: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`

First line of each file:
```json
{"type": "session_meta", "payload": {"id": "thread-id", "cwd": "/home/joe/project", "timestamp": "...", "git": {"branch": "main"}}}
```

Only the first line is read. If `type` is not `"session_meta"`, the file is skipped.

### API

```python
browser = SessionBrowser()
claude_sessions = browser.list_claude_sessions("/home/joe/myproject")  # For specific project
codex_sessions = browser.list_codex_sessions("/home/joe/myproject")    # Optional directory filter
recent = browser.list_recent(n=10, cli_filter="claude")                # Top N across all projects
cli_type = browser.detect_cli("session-uuid")                          # Returns "claude", "codex", or "unknown"
```

All methods return results sorted by timestamp descending (most recent first). All reads are non-destructive.
