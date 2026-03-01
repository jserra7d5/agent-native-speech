# Implementation Plan: Discord Voice Agent

**Branch**: `1-discord-voice-agent`
**Architecture**: MCP server + Claude Code plugin + Discord bot
**Pattern**: [CallMe](https://github.com/ZeframLou/call-me) — same 4 tools, Discord voice instead of phone, local GPU instead of cloud

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│  Claude Code (or any MCP client)                    │
│                                                     │
│  Tools available:                                   │
│    initiate_call(channel_id, message) → transcript  │
│    continue_call(call_id, message) → transcript     │
│    speak_to_user(call_id, message) → ack            │
│    end_call(call_id, message) → ack                 │
│    add_correction(wrong, right) → ack               │
│    list_corrections() → dict                        │
└──────────────────┬──────────────────────────────────┘
                   │ stdio (MCP JSON-RPC)
                   ▼
┌─────────────────────────────────────────────────────┐
│  MCP Server (Python)                                │
│                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ CallManager  │  │ STT Pipeline │  │ TTS Engine │ │
│  │             │  │              │  │            │ │
│  │ - sessions  │  │ - Silero VAD │  │ - Qwen3    │ │
│  │ - state     │  │ - Whisper    │  │ - resample │ │
│  │ - turns     │  │ - correction │  │ - encode   │ │
│  └──────┬──────┘  └──────────────┘  └────────────┘ │
│         │                                           │
│  ┌──────▼──────────────────────────────────────────┐│
│  │ Discord Bot (discord.py + voice-recv)           ││
│  │  - join/leave voice channels                    ││
│  │  - receive per-user audio via AudioSink         ││
│  │  - play audio via AudioSource                   ││
│  │  - post transcripts to text channel             ││
│  │  - slash commands (/correct, /corrections)      ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

---

## Project Structure

```
agent-native-speech/
├── server/
│   ├── __init__.py
│   ├── main.py              # MCP server entry point
│   ├── mcp_tools.py         # MCP tool definitions
│   ├── call_manager.py      # Session/call state management
│   ├── discord_bot.py       # Discord bot + voice handling
│   ├── audio_sink.py        # AudioSink for receiving Discord audio
│   ├── audio_source.py      # AudioSource for playing back to Discord
│   ├── stt_pipeline.py      # VAD + Whisper + correction pipeline
│   ├── tts_engine.py        # Qwen3-TTS wrapper + resampling
│   ├── correction.py        # Correction dictionary management
│   └── config.py            # Configuration loading
├── .claude-plugin/
│   ├── plugin.json           # Claude Code plugin manifest
│   └── README.md
├── data/
│   └── corrections/          # Per-user correction dictionaries
├── pyproject.toml
├── .env.example
└── specs/                    # This spec directory
```

---

## Phase 1: Foundation (Discord Bot + Audio Plumbing)

### Task 1.1: Project scaffolding
- Initialize pyproject.toml with dependencies
- Create virtual environment
- Set up .env.example with required variables (DISCORD_TOKEN, etc.)
- Create config.py for loading environment/config

**Dependencies**:
```
discord.py[voice] >= 2.5
discord-ext-voice-recv
faster-whisper
silero-vad (torch hub)
qwen-tts
flash-attn
librosa
soundfile
numpy
anthropic
mcp
python-dotenv
```

### Task 1.2: Discord bot core
- Create discord_bot.py with bot setup, slash command registration
- Implement `/connect` slash command → bot joins caller's voice channel
- Implement `/disconnect` slash command → bot leaves, cleans up
- Implement auto-disconnect when user leaves voice channel
- Run bot in a background thread (MCP server needs the main thread)

### Task 1.3: Audio receive pipeline
- Create audio_sink.py implementing `voice_recv.AudioSink`
- Receive per-user 48kHz stereo PCM frames
- Resample to 16kHz mono for VAD/Whisper
- Buffer audio chunks with configurable max duration
- **Test**: Verify audio receive works with DAVE-enabled channels

### Task 1.4: Audio playback pipeline
- Create audio_source.py implementing `discord.AudioSource`
- Accept 24kHz mono float32 (Qwen3 output)
- Resample to 48kHz stereo 16-bit PCM
- Feed 20ms frames (3,840 bytes) to Discord
- Implement `after` callback to signal playback complete
- **Test**: Play a pre-generated WAV file into a voice channel

---

## Phase 2: STT Pipeline (VAD + Whisper + Correction)

### Task 2.1: Silero VAD integration
- Load Silero VAD model (CPU, ~50MB)
- Implement VADIterator wrapper for streaming 16kHz chunks
- Detect speech start → begin accumulating frames
- Detect silence (configurable duration, default 1.5s) → speech segment complete
- Return accumulated speech buffer as numpy array

### Task 2.2: Faster-Whisper transcription
- Load model on GPU (configurable size: tiny/base/small/medium/large)
- Accept numpy array from VAD → transcribe
- Use `initial_prompt` with user's custom vocabulary
- Return raw transcript text

### Task 2.3: LLM post-correction
- Load user's correction dictionary from JSON file
- Build system prompt with correction examples
- Call Claude Haiku API with raw transcript + corrections
- Return corrected text
- Handle case where no corrections exist (pass through raw)

### Task 2.4: Full STT pipeline
- Wire together: audio_sink → resample → VAD → accumulate → Whisper → correct
- Create stt_pipeline.py that exposes `async listen() -> str`
- Blocks until user speaks + silence detected + transcription complete
- Returns corrected transcript text

---

## Phase 3: TTS Pipeline (Qwen3 + Discord Playback)

### Task 3.1: Qwen3-TTS engine
- Load model on GPU (1.7B CustomVoice with bfloat16 + FlashAttention2)
- Implement `synthesize(text, voice="Ryan") -> (numpy_array, sample_rate)`
- Handle code blocks: strip markdown code fences, optionally skip or summarize
- Handle long responses: split into sentences, synthesize incrementally

### Task 3.2: Audio format conversion
- Qwen3 outputs 24kHz mono float32
- Convert to 48kHz stereo 16-bit PCM for Discord
- Use librosa.resample for quality resampling
- Package as discord.AudioSource for playback

### Task 3.3: Full TTS pipeline
- Create tts_engine.py that exposes `async speak(text) -> None`
- Synthesize → resample → play via Discord voice client
- Block until playback completes
- Post text to Discord text channel simultaneously

---

## Phase 4: MCP Server + Tool Integration

### Task 4.1: MCP server scaffold
- Create main.py using `mcp` Python SDK (stdio transport)
- Register tool handlers
- Start Discord bot in background thread on server init
- Graceful shutdown: leave all voice channels, unload models

### Task 4.2: Call manager
- Create call_manager.py for session state
- Session = { call_id, channel_id, user_id, voice_client, conversation_history }
- One session per channel, keyed by call_id (UUID)
- Track session state: idle, listening, processing, speaking

### Task 4.3: MCP tool implementations

**`initiate_call(channel_id, message)`**:
1. Bot joins the specified Discord voice channel
2. Speak `message` via TTS
3. Listen for user response via STT
4. Return: `{call_id, transcript}`

**`continue_call(call_id, message)`**:
1. Look up active session by call_id
2. Speak `message` via TTS
3. Listen for user response via STT
4. Return: `{transcript}`

**`speak_to_user(call_id, message)`**:
1. Look up active session by call_id
2. Speak `message` via TTS (no listen)
3. Return: `{spoken: true}`

**`end_call(call_id, message)`**:
1. Look up active session by call_id
2. Speak goodbye `message` via TTS
3. Bot leaves voice channel
4. Clean up session
5. Return: `{duration_seconds}`

**`add_correction(wrong, right)`**:
1. Add to user's correction dictionary JSON
2. Return: `{added: true}`

**`list_corrections()`**:
1. Load user's correction dictionary
2. Return: `{corrections: {wrong: right, ...}}`

### Task 4.4: Text channel mirroring
- After each turn, post to the text channel:
  - User's transcribed text (with 🎤 prefix)
  - Agent's response text (with 🤖 prefix)
- Show typing indicator while agent is processing

---

## Phase 5: Claude Code Plugin Packaging

### Task 5.1: Plugin manifest
- Create `.claude-plugin/plugin.json` with MCP server configuration
- Define server command: `python server/main.py`
- List required environment variables
- Write plugin README

### Task 5.2: Installation flow
- `claude mcp add voice-agent python /path/to/server/main.py`
- Or via plugin: install from local directory
- Verify tools appear in Claude Code's tool list

### Task 5.3: Discord slash commands (supplementary)
- `/correct "wrong" "right"` — teach correction directly from Discord
- `/corrections` — list corrections
- `/config whisper_model "small"` — set STT model size
- `/config tts_voice "Ryan"` — set TTS voice

---

## Phase 6: Polish & Robustness

### Task 6.1: Error handling
- Agent subprocess crash recovery
- Discord disconnect/reconnect handling
- GPU OOM graceful degradation (fall back to smaller model)
- Network timeout handling for LLM correction calls

### Task 6.2: Configuration persistence
- Config file per server/user: `data/config/{user_id}.json`
- Correction dictionaries: `data/corrections/{user_id}.json`
- Session history: `data/sessions/{call_id}.json`

### Task 6.3: Code block handling in TTS
- Detect markdown code fences in agent output
- Options: skip entirely, speak "code block omitted", or summarize
- Detect and skip ASCII art, tables, long URLs

### Task 6.4: Latency optimization
- Pre-load models at server startup (not first call)
- Sentence-level TTS streaming (start speaking first sentence while synthesizing rest)
- Warm Whisper model with dummy transcription at startup

---

## Implementation Order & Dependencies

```
Phase 1 (Foundation)
  1.1 Scaffolding
  1.2 Discord bot ─────────────────────────┐
  1.3 Audio receive ──┐                    │
  1.4 Audio playback ─┤                    │
                      │                    │
Phase 2 (STT)        │                    │
  2.1 VAD ────────────┤                    │
  2.2 Whisper ────────┤                    │
  2.3 Correction ─────┤                    │
  2.4 STT pipeline ◄──┘                   │
                                           │
Phase 3 (TTS)                              │
  3.1 Qwen3 engine ───┐                   │
  3.2 Format convert ──┤                   │
  3.3 TTS pipeline ◄───┘                  │
                                           │
Phase 4 (MCP) ◄───────────────────────────┘
  4.1 MCP scaffold
  4.2 Call manager
  4.3 Tool implementations
  4.4 Text channel mirroring

Phase 5 (Plugin)
  5.1 Plugin manifest
  5.2 Installation flow
  5.3 Discord slash commands

Phase 6 (Polish)
  6.1 Error handling
  6.2 Config persistence
  6.3 Code block handling
  6.4 Latency optimization
```

---

## Testing Strategy

1. **Unit**: Each pipeline component in isolation (VAD, Whisper, TTS, resampling)
2. **Integration**: Full STT pipeline with recorded audio files
3. **Integration**: Full TTS pipeline → Discord playback with a test bot
4. **End-to-end**: MCP tool call → Discord voice → hear response
5. **DAVE verification**: Test audio receive on encrypted voice channel (critical, do first)
6. **Latency benchmark**: Measure end-of-speech to start-of-response time

## Quickstart (Developer Setup)

```bash
# Clone and setup
git clone <repo>
cd agent-native-speech
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env: DISCORD_TOKEN, ANTHROPIC_API_KEY

# Run MCP server standalone (for testing)
python server/main.py

# Add to Claude Code
claude mcp add voice-agent python server/main.py
```
