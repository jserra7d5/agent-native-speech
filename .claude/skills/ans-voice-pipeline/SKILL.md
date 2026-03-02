---
name: ans-voice-pipeline
description: Voice conversation pipeline for agent-native-speech. Covers TTS synthesis, STT transcription, audio format conversion, VAD, speech modes, and correction. Use this skill when working on any voice I/O code -- the speak/listen loop, audio format bugs, TTS/STT backend changes, or speech mode behavior.
---

# Voice Pipeline

The core voice I/O loop that powers agent-native-speech. Handles everything between "agent wants to say something" and "agent receives transcribed user speech": text preprocessing, TTS synthesis, audio format conversion for Discord, voice reception, VAD-based speech detection, transcription, and LLM-assisted correction.

## File Map

| File | Role | Key Types |
|---|---|---|
| `server/call_manager.py` | Orchestrator -- bridges MCP tools to voice ops | `CallSession`, `CallManager` |
| `server/tts_backend.py` | TTSBackend protocol + shared text preprocessing | `TTSBackend` (Protocol), `preprocess()` |
| `server/tts_engine.py` | Local Qwen3-TTS (preset voices + voice cloning) | `TTSEngine` |
| `server/elevenlabs_tts.py` | ElevenLabs cloud TTS | `ElevenLabsTTSEngine` |
| `server/audio_source.py` | TTS output to Discord playback format | `TTSAudioSource`, `StreamingAudioSource` |
| `server/audio_sink.py` | Discord audio reception to STT input format | `UserAudioSink`, `MultiUserAudioSink` |
| `server/vad.py` | Silero VAD speech boundary detection | `SpeechDetector`, `SpeechEvent` |
| `server/stt_pipeline.py` | Full STT orchestration: sink + VAD + transcribe + correct | `STTPipeline` |
| `server/transcriber.py` | Faster-Whisper local transcription | `Transcriber`, `TranscriptionResult` |
| `server/elevenlabs_stt.py` | ElevenLabs Scribe cloud STT | `ElevenLabsTranscriber` |
| `server/speech_mode.py` | Pause vs stop_token completion modes | `SpeechModeManager` |
| `server/correction.py` | Per-user correction dictionary + LLM correction | `CorrectionManager` |
| `server/voice_profile.py` | Voice profile registry (presets + clones) | `VoiceProfile`, `VoiceProfileRegistry` |

## Data Flow

```
Agent text
  |
  v
CallManager._tts_speak()
  |-- preprocess(): strip code blocks, split sentences
  |-- TTSBackend.synthesize() or synthesize_streamed()
  |     Returns (float32 mono ndarray, sample_rate)
  |-- TTSAudioSource.from_audio(): resample to 48kHz, mono->stereo, float32->int16
  |-- voice_client.play(source)
  v
Discord voice channel
  |
  v
CallManager._stt_listen()
  |-- STTPipeline.listen()
  |     |-- UserAudioSink: 48kHz stereo int16 -> 16kHz mono float32
  |     |-- SpeechDetector.process_chunk(): VAD speech boundary detection
  |     |-- Transcriber.transcribe(): Whisper or ElevenLabs Scribe
  |     |-- CorrectionManager.correct(): LLM-based fix-up
  v
Corrected transcript string
```

## Key Architectural Patterns

### TTS Output Contract
All TTS backends return `tuple[np.ndarray, int]` -- a float32 mono ndarray and its sample rate. `audio_source.py` handles all conversion to Discord format. Never return pre-converted PCM from a TTS backend.

### Streaming vs Non-Streaming TTS
CallManager chooses the path based on sentence count:
- **1 sentence**: `synthesize()` -> `TTSAudioSource.from_audio()` -> play. Simple, no overhead.
- **2+ sentences**: `synthesize_streamed()` -> `StreamingAudioSource`. Background thread synthesizes chunks and feeds them via `add_segment()`. Discord starts playing the first chunk while later chunks are still synthesizing.

StreamingAudioSource returns silence frames (`b"\x00" * 3840`) when the reader catches up to the writer, keeping Discord's audio thread alive without stalling.

### Mutual-Exclusion Model Loading (Local TTS)
Only one Qwen3-TTS model stays in VRAM at a time. Loading the CustomVoice model unloads the Base model, and vice versa. This keeps total VRAM under budget on a single GPU.

### VAD Silence Injection
Discord stops sending audio packets when the user stops speaking. The STT pipeline injects synthetic silence chunks into the VAD after 300ms of no audio, so the VAD's silence counter can accumulate and trigger the end-of-speech event.

### Lazy Model Loading
Both Whisper and Qwen3-TTS models load on first use, not at construction. This keeps server startup fast and lets the warmup step happen explicitly.

### LLM Correction Pre-Filter
Before calling Claude for transcript correction, `CorrectionManager` checks if any correction key appears as a substring in the transcript. If not, the API call is skipped entirely -- no cost for clean transcripts.

## Speech Modes

Two modes control how the STT pipeline decides the user has finished speaking:

| Mode | Trigger | Use Case |
|---|---|---|
| `pause` (default) | VAD detects silence exceeding `silence_duration_ms` | Short back-and-forth conversation |
| `stop_token` | User says configured stop word (default: "over") | Long-form dictation or instructions |

In stop_token mode, the pipeline runs an inner loop: listen for a segment, transcribe it, check for stop word at end, accumulate. Segments are joined with spaces. The stop word is stripped from the final transcript.

## Audio Format Quick Reference

| Stage | Sample Rate | Channels | Bit Depth | Format |
|---|---|---|---|---|
| Discord playback | 48 kHz | Stereo | 16-bit int | 3840 bytes/frame (20ms) |
| TTS engine output | 24 kHz | Mono | float32 | numpy ndarray |
| ElevenLabs TTS output | 24 kHz | Mono | float32 | numpy ndarray (converted from pcm_24000) |
| Discord reception | 48 kHz | Stereo | 16-bit int | 3840 bytes/frame (20ms) |
| STT input / VAD input | 16 kHz | Mono | float32 | numpy ndarray |
| Silero VAD window | 16 kHz | Mono | float32 | 512 samples (32ms) |

## Config Structures

```python
# Relevant config dataclasses (server/config.py)
STTConfig:   backend, model, device, compute_type, elevenlabs_*
TTSConfig:   backend, default_voice, device, voices_dir, elevenlabs_*
VADConfig:   silence_duration_ms (default 1500), threshold (default 0.5)
SpeechModeConfig: mode ("pause"/"stop_token"), stop_word, max_timeout_s
CorrectionConfig: model (override), data_dir (Path)
```

## Common Tasks

**Adding a new TTS backend**: Implement the `TTSBackend` protocol from `tts_backend.py`. Must provide `synthesize()`, `synthesize_streamed()`, `warmup()`, `unload()`, and `is_loaded`. Return `(float32_mono_ndarray, sample_rate)`. Add backend selection logic in `server/main.py` where the TTS engine is constructed.

**Adding a new STT backend**: Match the `Transcriber` interface -- `transcribe(audio, initial_prompt) -> TranscriptionResult`, plus `build_initial_prompt()`, `warmup()`, `unload()`, `is_loaded`. Add backend selection in `STTPipeline.__init__()`.

**Tuning VAD sensitivity**: Adjust `VADConfig.threshold` (lower = more sensitive, range 0-1) and `silence_duration_ms` (how long silence must persist to trigger end-of-speech). The silence injection delay is hardcoded at 300ms in `stt_pipeline.py`.

**Debugging audio issues**: STT pipeline saves debug audio to `/tmp/voice-agent-debug.wav` after each listen. Check this file to verify audio is reaching the pipeline correctly.

## Detailed References

- `references/tts-pipeline.md` -- TTS backends, text preprocessing, voice profiles, streaming
- `references/stt-pipeline.md` -- STT pipeline, VAD internals, transcription, correction system
- `references/audio-formats.md` -- Audio source/sink conversion, Discord format requirements, threading
