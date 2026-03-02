---
name: elevenlabs-voice-setup
description: Set up ElevenLabs cloud TTS and STT in agent-native-speech, create and manage custom voices via IVC or text-to-voice, and configure the voice pool. Use this skill when integrating ElevenLabs, creating/cloning voices, or troubleshooting ElevenLabs configuration.
---

# ElevenLabs Voice Setup

## Overview

This skill covers end-to-end ElevenLabs integration with agent-native-speech: configuring cloud TTS/STT backends, creating custom voices (instant voice cloning or text-to-voice design), managing the voice pool for multi-session use, and selecting the right model for your latency/quality/cost tradeoffs.

## Quick Start: Enable ElevenLabs in agent-native-speech

### 1. Install the SDK

```bash
source .venv/bin/activate && pip install elevenlabs
```

### 2. Get an API key

Sign up at https://elevenlabs.io and copy your API key from Profile > API Keys.

### 3. Configure config.json

Minimal config to switch both TTS and STT to ElevenLabs:

```json
{
  "elevenlabs_api_key": "sk_...",
  "stt": {
    "backend": "elevenlabs",
    "elevenlabs": {
      "model_id": "scribe_v2",
      "language_code": "eng"
    }
  },
  "tts": {
    "backend": "elevenlabs",
    "default_voice": "Ryan",
    "elevenlabs": {
      "model_id": "eleven_flash_v2_5",
      "default_voice_id": "CYDzJWiIyIiQuhRB4r1K",
      "voices": {
        "Ryan": "CYDzJWiIyIiQuhRB4r1K"
      }
    }
  }
}
```

**Key config fields:**
- `elevenlabs_api_key` -- top-level, shared by TTS and STT
- `tts.backend` -- set to `"elevenlabs"` (default is `"local"` for Qwen3-TTS)
- `tts.elevenlabs.default_voice_id` -- fallback voice ID for all sessions
- `tts.elevenlabs.model_id` -- TTS model (see Models section below)
- `tts.elevenlabs.voices` -- name-to-voice-ID map for the voice pool
- `stt.backend` -- set to `"elevenlabs"` (default is `"local"` for Whisper)
- `stt.elevenlabs.model_id` -- STT model (`scribe_v2`)

Config is validated on startup. Required when using ElevenLabs TTS: `elevenlabs_api_key` and at least one of `default_voice_id` or `voices`.

### 4. Verify

```bash
source .venv/bin/activate && python -m server.main
# In another terminal:
curl http://127.0.0.1:8765/health
```

## Voice Creation Workflows

### Option A: Instant Voice Clone (IVC)

Clone a voice from audio samples. Best when you have a recording of the target voice.

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

voice = client.voices.ivc.create(
    name="My Custom Voice",
    description="Warm, professional male narrator",
    files=[open("sample.wav", "rb")],
)
print(f"Voice ID: {voice.voice_id}")
```

Then add it to config.json:

```json
"voices": {
  "CustomVoice": "<voice_id_from_above>"
}
```

Audio requirements: 192kbps+, 60s+ continuous speech, clean recording.
See `references/voice-cloning.md` for full details, best practices, and multi-file cloning.

### Option B: Text-to-Voice Design

Create a voice from a natural language description -- no audio samples needed.

```python
voice = client.text_to_voice.create(
    voice_description="A calm, deep-voiced British man in his 40s, speaking slowly and clearly",
    text="Hello, I am your AI assistant. How can I help you today?",
)
print(f"Voice ID: {voice.voice_id}")
```

See `references/voice-design.md` for tips on writing effective voice descriptions.

### After Creating a Voice

1. Copy the `voice_id` from the creation response
2. Add a name mapping in `config.json` under `tts.elevenlabs.voices`
3. Optionally add the name to the `voice_pool` array for multi-session assignment
4. Restart the server

## Voice Pool Configuration

The voice pool assigns distinct voices to concurrent agent sessions so users can tell agents apart. Configure it in config.json:

```json
{
  "tts": {
    "default_voice": "Ryan",
    "elevenlabs": {
      "voices": {
        "Ryan": "CYDzJWiIyIiQuhRB4r1K",
        "Aiden": "pNInz6obpgDQGcFmaJgB",
        "CustomVoice": "<your_voice_id>",
        "system": "21m00Tcm4TlvDq8ikWAM"
      }
    }
  },
  "voice_pool": ["Ryan", "Aiden", "CustomVoice"],
  "system_voice": "system"
}
```

**How it works:**
- Single session: uses `tts.default_voice` (no pool assignment)
- Multiple sessions: each gets the next unassigned voice from `voice_pool`
- `system_voice` is reserved for switchboard announcements, never assigned to agents
- Voice names in `voice_pool` must have corresponding entries in `tts.elevenlabs.voices`
- Raw ElevenLabs voice IDs (16+ alphanumeric chars) can be passed directly, bypassing the name map

## Voice Management

List, inspect, edit, and delete voices via the SDK:

```python
from elevenlabs import ElevenLabs
client = ElevenLabs(api_key="sk_...")

# List all voices
voices = client.voices.search()
for v in voices.voices:
    print(f"{v.name}: {v.voice_id}")

# Delete a voice
client.voices.delete(voice_id="...")
```

See `references/voice-management.md` for editing voices, voice settings (stability, similarity_boost, style), and label management.

## Models

| Model | Latency | Quality | Cost | Languages |
|---|---|---|---|---|
| `eleven_v3` | Standard | Highest | 1.0x | 70+ |
| `eleven_multilingual_v2` | Standard | High | 1.0x | 29 |
| `eleven_flash_v2_5` | Ultra-low | Good | 0.5x | 32 |
| `eleven_turbo_v2_5` | Low | Good | 0.5x | 32 |

**Recommendations:**
- Real-time voice calls: `eleven_flash_v2_5` (lowest latency, half cost)
- Maximum quality/expressiveness: `eleven_v3`
- Budget-conscious with good quality: `eleven_flash_v2_5` or `eleven_turbo_v2_5`

See `references/models-and-pricing.md` for detailed comparisons, rate limits, and pricing tiers.

## Codebase Reference

Key source files in `server/`:

| File | Purpose |
|---|---|
| `elevenlabs_tts.py` | `ElevenLabsTTSEngine` -- cloud TTS via SDK, outputs float32 mono 24kHz |
| `elevenlabs_stt.py` | `ElevenLabsTranscriber` -- cloud STT via Scribe API, 16kHz input |
| `voice_pool.py` | `VoicePool` -- per-session voice assignment from pool |
| `config.py` | `Config`, `TTSConfig`, `STTConfig` dataclasses with validation |
| `tts_backend.py` | `TTSBackend` protocol + `preprocess()` text cleaning |

**TTS engine internals:**
- Lazy client init via `_get_client()` (import happens on first use)
- Voice resolution: name map lookup -> raw ID passthrough -> default fallback
- Output format: `pcm_24000` (signed 16-bit LE) converted to float32 `[-1, 1]`
- Streaming: `synthesize_streamed()` buffers ~0.5s chunks before yielding

**STT engine internals:**
- Input: 16kHz mono float32 numpy array
- Converts to 16-bit PCM bytes, sends via `client.speech_to_text.convert()`
- No initial prompt support (corrections applied post-transcription by pipeline)

## Troubleshooting

| Problem | Solution |
|---|---|
| `elevenlabs_api_key is required` | Set `elevenlabs_api_key` at top level of config.json |
| `Either default_voice_id or voices is required` | Add `tts.elevenlabs.default_voice_id` or `tts.elevenlabs.voices` |
| 401 Unauthorized | Check API key is valid and not expired |
| 429 Too Many Requests | Character quota exceeded; upgrade plan or wait for reset |
| Audio sounds robotic/choppy | Try `eleven_v3` model or adjust voice settings |
| Voice not found in pool | Ensure name in `voice_pool` has matching entry in `tts.elevenlabs.voices` |
| STT returns empty text | Check audio is >0.1s, verify `stt.elevenlabs.model_id` is `scribe_v2` |

## References

- `references/voice-cloning.md` -- IVC workflow, audio requirements, multi-file cloning, best practices
- `references/voice-design.md` -- text-to-voice creation, writing voice descriptions, API details
- `references/voice-management.md` -- list, get, edit, delete voices, voice settings and labels
- `references/models-and-pricing.md` -- model comparison, rate limits, quotas, pricing tiers
