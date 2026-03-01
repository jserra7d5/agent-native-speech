# Research: Discord Voice Agent

## Decision 1: Architecture — MCP Server (not subprocess wrapper)

**Decision**: Build as an MCP server + Claude Code plugin, following the CallMe pattern.

**Rationale**: The [CallMe](https://github.com/ZeframLou/call-me) project proves that voice I/O works elegantly as an MCP server. The agent doesn't need to know about STT/TTS — it just "talks" to the user via tool calls. This is cleaner than subprocess wrapping and works with any MCP-compatible client (Claude Code, Claude Desktop, Cursor, etc.).

**Key insight from CallMe**: Tools are blocking. `initiate_call(message)` speaks a message, listens for the user's response, transcribes it, and returns the text. The agent never touches audio directly.

**Alternatives considered**:
- Subprocess wrapper (stdin/stdout piping) — too tightly coupled, agent-specific
- VoiceMode MCP (github.com/mbailey/voicemode) — microphone-based, not Discord
- Full framework (Pipecat/LiveKit) — overkill for turn-based conversation

## Decision 2: Discord Library — discord.py + discord-ext-voice-recv

**Decision**: Use discord.py (not Pycord) with the discord-ext-voice-recv extension.

**Rationale**: discord.py has DAVE protocol support merged (PR #10300, Jan 2026) via the `davey` package. Pycord has no confirmed DAVE implementation. The DAVE mandate is effective March 1-2, 2026.

**Audio format details**:
- Receive: 48kHz, 16-bit signed, stereo PCM, 20ms frames (3,840 bytes/frame)
- Playback: Same format, via `discord.PCMAudio` or `discord.FFmpegPCMAudio`
- Per-user audio via `AudioSink.write(user, data)` — SSRC identifies speakers
- discord-ext-voice-recv inherits discord.py's DAVE transport layer

**Alternatives considered**:
- Pycord — better native voice receive but no DAVE support (dealbreaker)
- discord.js — DAVE ready, but adds Node.js dependency alongside Python ML stack

## Decision 3: STT — Faster-Whisper with Silero VAD

**Decision**: Use Faster-Whisper (CTranslate2) with Silero VAD for voice activity detection.

**Rationale**: Faster-Whisper is 4x faster than OpenAI's Whisper at same accuracy. Silero VAD is the de-facto standard for speech endpoint detection (enterprise-grade, open-source).

**Integration details**:
- Discord delivers 48kHz stereo → resample to 16kHz mono for both VAD and Whisper
- Silero VAD: feed 512-sample chunks (32ms at 16kHz), detects speech start/end
- Accumulate speech frames → when VAD detects silence → send accumulated buffer to Whisper
- Whisper `initial_prompt` biases toward custom vocabulary (224 token limit)
- GPU VRAM: ~1GB (base), ~2GB (small), ~5GB (medium)

**Alternatives considered**:
- Deepgram Nova-3 — excellent but cloud-only (violates local privacy goal)
- WhisperX — adds complexity with alignment/diarization we don't need

## Decision 4: Post-Correction — LLM with learned dictionary

**Decision**: Run raw Whisper output through Claude Haiku with a user-specific correction dictionary.

**Rationale**: Whisper's `initial_prompt` is limited to 224 tokens and has ~30% success rate for rare terms. An LLM correction pass with explicit examples is more reliable and learns over time.

**Implementation**:
- Correction dictionary: JSON file per user `{wrong_phrase: correct_phrase}`
- System prompt includes dictionary entries as few-shot examples
- LLM applies corrections contextually (not naive find-replace)
- `/correct "wrong" "right"` Discord slash command to teach corrections
- Cost: ~$0.001 per correction pass (Haiku)

**Alternatives considered**:
- Whisper fine-tuning — effective but requires training data and GPU time for each user
- Naive find-replace — breaks on partial matches and context-dependent terms

## Decision 5: TTS — Qwen3-TTS (local GPU)

**Decision**: Use Qwen3-TTS 1.7B CustomVoice model running locally.

**Rationale**: 97ms time-to-first-audio, streaming support, 9 preset voices, emotion/tone control via `instruct` parameter. Beats ElevenLabs in quality benchmarks while running fully local.

**Integration details**:
- Install: `pip install -U qwen-tts` (+ flash-attn for 30-40% speedup)
- Output: 24kHz mono float32 → resample to 48kHz stereo for Discord
- Streaming: Supported via `faster-qwen3-tts` for chunked generation
- GPU VRAM: 6-8GB (1.7B with bfloat16 + FlashAttention2)
- Voices: Ryan, Aiden (English), Vivian, Serena, Dylan, Eric (Chinese), + others
- Code blocks in agent output: skip or summarize (not speak verbatim)

**Alternatives considered**:
- Kokoro-82M — faster but lower quality, no emotion control
- ElevenLabs — high quality but cloud-only
- Piper — instant but robotic quality

## Decision 6: Agent Integration — MCP (generic) + Claude Code plugin

**Decision**: Build as a generic MCP server that also ships as a Claude Code plugin.

**Rationale**: MCP server makes it work with any MCP client. Plugin packaging makes Claude Code installation one command. CallMe demonstrates this dual approach with `.claude-plugin/` + `server/`.

**Tool design (following CallMe pattern)**:
- `initiate_call(channel_id, message)` — join VC, speak, listen, return transcript
- `continue_call(call_id, message)` — speak follow-up, listen, return transcript
- `speak_to_user(call_id, message)` — speak without waiting for response
- `end_call(call_id, message)` — speak goodbye, leave VC, cleanup
- `add_correction(wrong, right)` — teach a vocabulary correction
- `list_corrections()` — show current correction dictionary

**Claude Code headless mode** (alternative for non-MCP agents):
- `claude -p "message" --resume SESSION_ID --output-format json`
- Supports multi-turn via `--resume` / `-c`
- JSON output with session_id for tracking

## Decision 7: Language — Python (not TypeScript)

**Decision**: Build in Python despite CallMe being TypeScript/Bun.

**Rationale**: All ML dependencies (Faster-Whisper, Qwen3-TTS, Silero VAD, torch) are Python-native. The MCP SDK has a Python implementation. discord.py is Python. Forcing TypeScript would mean bridging to Python for every ML operation.

**MCP SDK**: Use `mcp` Python package (official SDK) or FastMCP for ergonomics.

## Decision 8: GPU Memory Management

**Decision**: Load STT and TTS models on the same GPU, use model size configuration to fit VRAM budget.

**Estimated VRAM usage**:
- Faster-Whisper base (float16): ~1GB
- Faster-Whisper small (float16): ~2GB
- Qwen3-TTS 1.7B (bfloat16 + FA2): ~6-8GB
- Silero VAD: ~50MB (CPU-based, negligible)
- Total: ~7-10GB depending on Whisper model size

**RTX 3090/4090 (24GB)**: Comfortable with large Whisper + 1.7B TTS
**RTX 3080/4080 (10-16GB)**: Use base/small Whisper + 1.7B TTS
**RTX 3060 (12GB)**: Use base Whisper + 0.6B TTS variant

## Key Risk: DAVE Protocol + discord-ext-voice-recv

**Risk**: discord-ext-voice-recv may not work with DAVE-encrypted voice channels.

**Mitigation**:
- discord-ext-voice-recv sits on top of discord.py's voice transport layer
- discord.py handles DAVE decryption before audio reaches the AudioSink
- Audio data in the sink should already be decrypted
- Needs verification: test with a DAVE-enabled channel immediately
- Fallback: If broken, contribute DAVE compatibility to discord-ext-voice-recv or handle raw Opus decoding ourselves from discord.py's voice websocket
