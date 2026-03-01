# Feature Specification: Discord Voice Agent

**Status**: Draft
**Created**: 2026-02-28
**Branch**: `1-discord-voice-agent`

---

## Overview

An MCP server and Claude Code plugin that gives AI agents the ability to "call" a user on Discord. The agent initiates a voice call, speaks its message via TTS, and listens for the user's spoken response via STT — all through Discord voice channels. The architecture follows the [CallMe](https://github.com/ZeframLou/call-me) pattern: the agent uses simple tool calls (`initiate_call`, `continue_call`, `speak_to_user`, `end_call`) and never touches audio directly. Speech is transcribed with learned vocabulary corrections, and agent responses are synthesized locally on GPU.

The system learns the user's vocabulary over time through an explicit correction mechanism, improving transcription accuracy across sessions.

## Problem Statement

CLI AI agents (Claude Code, aider, etc.) are text-only interfaces. Users who want to interact with these agents hands-free — while working on hardware, whiteboarding, or multitasking — have no natural way to do so. Existing voice assistants are tightly coupled to specific LLMs and don't support arbitrary CLI agent backends.

Discord provides a familiar, cross-platform voice transport that users already have running, eliminating the need for custom audio infrastructure.

## Target Users

- **Primary**: Developers who use CLI AI agents and want hands-free interaction
- **Secondary**: Team members who want to observe or participate in agent conversations via a shared Discord channel

## User Scenarios & Acceptance Criteria

### Scenario 1: Starting a Voice Session

**As a** developer in a Discord server,
**I want to** join a voice channel and start talking to my AI agent,
**so that** I can interact with the agent hands-free.

**Acceptance Criteria:**
- User joins a Discord voice channel
- User invokes the bot via a slash command (e.g., `/connect`)
- Bot joins the voice channel and begins listening
- Bot provides an audible or visual confirmation that it's ready
- Bot starts a new CLI agent subprocess in the background

### Scenario 2: Speaking a Request

**As a** user in an active voice session,
**I want to** speak my request naturally and have it understood correctly,
**so that** the agent receives an accurate text representation of what I said.

**Acceptance Criteria:**
- Bot detects when the user begins and stops speaking (voice activity detection)
- After the user stops speaking, the audio is transcribed to text
- Transcription is corrected using the user's custom vocabulary and correction dictionary
- The corrected text is displayed in a Discord text message for transparency
- The corrected text is forwarded to the CLI agent subprocess

### Scenario 3: Hearing the Agent's Response

**As a** user waiting for the agent's response,
**I want to** hear the response spoken aloud in the voice channel,
**so that** I can continue working hands-free.

**Acceptance Criteria:**
- The agent's text output is captured from its subprocess
- The text is synthesized into speech
- The speech audio is played back into the Discord voice channel
- The agent's text response is also posted in the Discord text channel for reference
- After playback completes, the bot returns to listening mode

### Scenario 4: Teaching a Correction

**As a** user whose domain jargon is being misrecognized,
**I want to** teach the system the correct transcription for specific words or phrases,
**so that** future transcriptions are more accurate.

**Acceptance Criteria:**
- User runs a slash command (e.g., `/correct "pipe cat" "Pipecat"`)
- The correction is stored persistently and associated with the user
- Future transcriptions apply the correction automatically
- User can list, update, and remove corrections
- Corrections persist across bot restarts and sessions

### Scenario 5: Ending a Session

**As a** user finishing a voice session,
**I want to** disconnect cleanly,
**so that** the agent subprocess is terminated and resources are freed.

**Acceptance Criteria:**
- User runs a slash command (e.g., `/disconnect`) or leaves the voice channel
- Bot terminates the CLI agent subprocess gracefully
- Bot leaves the voice channel
- Session conversation history is preserved for future reference

### Scenario 6: Configuring the Agent

**As a** user or server admin,
**I want to** configure which CLI agent the bot uses and how it behaves,
**so that** I can use my preferred agent and settings.

**Acceptance Criteria:**
- Configuration supports specifying the CLI agent command (e.g., `claude --print`, `aider`)
- Configuration supports custom vocabulary/initial prompt for transcription biasing
- Configuration supports selecting the speech-to-text model size
- Configuration supports selecting the text-to-speech voice
- Configuration can be set per-user or per-server
- Configuration persists across bot restarts

## Functional Requirements

### FR-1: Discord Voice Transport

- FR-1.1: Bot connects to and disconnects from Discord voice channels on command
- FR-1.2: Bot receives audio from users in the voice channel
- FR-1.3: Bot plays synthesized audio back into the voice channel
- FR-1.4: Bot limits to one active session per voice channel
- FR-1.5: Bot auto-disconnects when the user leaves the voice channel

### FR-2: Speech-to-Text Pipeline

- FR-2.1: Incoming audio is decoded from Discord's format to PCM suitable for the speech recognition model
- FR-2.2: Voice activity detection determines when the user starts and stops speaking
- FR-2.3: Detected speech segments are transcribed to text using a local speech recognition model
- FR-2.4: Transcription is biased toward the user's custom vocabulary via an initial prompt mechanism
- FR-2.5: Raw transcription undergoes a post-correction pass using the user's correction dictionary
- FR-2.6: The correction dictionary is a persistent, user-specific JSON store of `wrong → right` mappings
- FR-2.7: The post-correction pass uses an LLM to apply corrections contextually (not just naive find-replace)

### FR-3: CLI Agent Bridge

- FR-3.1: Bot spawns a configurable CLI agent as a subprocess
- FR-3.2: Corrected transcription text is sent to the agent's standard input
- FR-3.3: Agent's standard output is captured as the response text
- FR-3.4: Bot handles agent responses that arrive incrementally (streaming output)
- FR-3.5: Bot handles agent subprocess crashes or timeouts gracefully
- FR-3.6: Agent subprocess is terminated when the session ends

### FR-4: Text-to-Speech Pipeline

- FR-4.1: Agent response text is synthesized into audio using a local TTS model
- FR-4.2: Synthesized audio is encoded into a format suitable for Discord voice playback
- FR-4.3: Long responses are streamed incrementally (sentence-by-sentence) rather than waiting for full synthesis
- FR-4.4: Code blocks and non-speech content in agent responses are handled appropriately (skipped or summarized)

### FR-5: Session Management

- FR-5.1: Each voice session maintains conversation context for the duration of the connection
- FR-5.2: The bot posts both the user's transcribed text and the agent's response text in the Discord text channel
- FR-5.3: The bot shows typing/processing indicators in Discord while the agent is working
- FR-5.4: The turn-based flow enforces: listen → transcribe → correct → agent → synthesize → speak → listen

### FR-6: Correction Dictionary Management

- FR-6.1: Users can add corrections via `/correct "wrong" "right"`
- FR-6.2: Users can list their corrections via `/corrections`
- FR-6.3: Users can remove corrections via `/uncorrect "wrong"`
- FR-6.4: Corrections are stored per-user and persist across sessions
- FR-6.5: The initial vocabulary prompt is automatically augmented with correction dictionary entries

### FR-7: Configuration

- FR-7.1: Agent command is configurable (default: `claude --print`)
- FR-7.2: STT model size is configurable (options: tiny, base, small, medium, large)
- FR-7.3: TTS voice is configurable
- FR-7.4: Custom vocabulary / initial prompt is configurable per user or per server
- FR-7.5: Configuration is managed via slash commands or a config file
- FR-7.6: Configuration persists across bot restarts

## Non-Functional Requirements

- **Latency**: Total round-trip time from end of user speech to start of bot speech should be under 5 seconds for typical utterances (1-2 sentences)
- **Reliability**: Bot should recover gracefully from agent subprocess crashes without requiring a full restart
- **Privacy**: All audio processing (STT and TTS) happens locally; no audio is sent to external services
- **Concurrency**: Bot supports multiple simultaneous sessions across different voice channels (one session per channel)
- **Resource Usage**: STT and TTS models share the local GPU; the system must manage GPU memory to avoid OOM errors

## Key Entities

- **Session**: An active voice conversation between a user and an agent in a specific voice channel. Contains conversation history, active subprocess, and channel references.
- **Correction Dictionary**: A per-user JSON store of `wrong_phrase → correct_phrase` mappings used during post-correction.
- **Configuration**: Per-user and per-server settings including agent command, model sizes, voice selection, and custom vocabulary.
- **Turn**: A single exchange in the conversation: user utterance → agent response.

## Scope Boundaries

### In Scope
- Single-user voice interaction per channel (the user who invoked the bot)
- Turn-based conversation (not full-duplex/simultaneous speech)
- Local STT and TTS processing on GPU
- CLI agent subprocess management
- Persistent correction dictionary and configuration
- Text channel mirroring of voice conversation

### Out of Scope
- Multi-user conversations (multiple people talking to the same agent)
- Real-time / full-duplex conversation (both parties speaking simultaneously)
- Voice cloning or custom voice training
- Web UI or mobile app
- Agent-to-agent voice communication
- Telephony / PSTN integration
- Video or screen-sharing integration

## Assumptions

- The host machine has an NVIDIA GPU with sufficient VRAM to run both the STT model and TTS model concurrently (estimated 6-8 GB minimum for medium STT + TTS)
- The user has a Discord bot token and appropriate server permissions
- The CLI agent being used supports a non-interactive mode (accepts input via stdin, produces output via stdout)
- Discord's DAVE (end-to-end encryption) protocol is supported via discord.py (PR #10300 merged Jan 2026) and the `davey` package
- Network latency to Discord's voice servers is acceptable (< 100ms)
- The user is the only person speaking in the voice channel during an active session

## Dependencies

- Discord Bot API and voice gateway access
- discord.py (with DAVE protocol support) + discord-ext-voice-recv (for audio receive via AudioSink)
- A local speech-to-text model (e.g., Faster-Whisper)
- A local text-to-speech model (e.g., Qwen3-TTS)
- A voice activity detection model (e.g., Silero VAD)
- An LLM API for post-correction (e.g., Claude Haiku)
- A CLI agent that supports non-interactive stdin/stdout usage
- NVIDIA GPU with CUDA support

## Success Criteria

1. A user can start a voice session, speak a request, and hear the agent's response within 5 seconds of finishing their utterance (for typical 1-2 sentence inputs)
2. Transcription accuracy for the user's domain-specific vocabulary improves measurably after adding corrections (target: 90%+ accuracy on corrected terms)
3. The system successfully completes a 30-minute voice conversation session without crashes or resource exhaustion
4. Users can switch between different CLI agents without modifying the bot's code
5. Conversation transcripts posted in the text channel are accurate and readable
6. The system handles the user leaving the voice channel mid-session without orphaning processes or leaking resources

## Resolved Questions

- **DAVE Protocol**: Resolved. discord.py has DAVE support merged (PR #10300, Jan 2026) using the `davey` package. Pycord has no confirmed DAVE implementation and is not viable. The project will use discord.py + discord-ext-voice-recv, which inherits discord.py's DAVE transport layer.
