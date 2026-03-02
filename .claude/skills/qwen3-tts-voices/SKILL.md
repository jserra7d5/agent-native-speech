---
name: qwen3-tts-voices
description: Qwen3-TTS custom voice creation for agent-native-speech. Covers model selection, voice cloning from reference audio, voice design from text descriptions, preset speakers, fine-tuning, and integration with the local TTS engine. Use this skill when creating new voice profiles, downloading or switching Qwen3-TTS models, cloning voices, designing voices from descriptions, or fine-tuning models on custom datasets.
---

# Qwen3-TTS Custom Voices

Create and manage custom voices for agent-native-speech's local TTS engine. This covers model downloads, voice cloning from reference audio, voice design from text descriptions, preset speaker configuration, and fine-tuning on custom datasets.

## Model Selection Decision Tree

```
What do you need?
|
+-- Clone an existing person's voice from audio?
|   |-- Need lightweight / fast inference?
|   |   --> 0.6B-Base  (~1.2GB VRAM)
|   |-- Need highest quality cloning?
|       --> 1.7B-Base  (~3.5GB VRAM)  [default in agent-native-speech]
|
+-- Design a new voice from a text description?
|   --> 1.7B-VoiceDesign  (~3.5GB VRAM)
|
+-- Use a built-in preset speaker with emotion/style control?
|   |-- Need lightweight?
|   |   --> 0.6B-CustomVoice  (~1.2GB VRAM)
|   |-- Need best quality?
|       --> 1.7B-CustomVoice  (~3.5GB VRAM)  [default in agent-native-speech]
|
+-- Fine-tune a model on your own dataset?
    --> Start with 1.7B-Base, fine-tune with community tool
```

All models require the **Qwen3-TTS-Tokenizer-12Hz** (~2GB) as a dependency. It downloads automatically with `qwen-tts`, or can be pre-downloaded manually.

**Supported languages**: Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian.

See `references/model-downloads.md` for all model IDs, sizes, and download commands.

## Quick Start: Add a Voice to agent-native-speech

### Option A: Clone a Voice from Audio

1. Record or obtain 5-30 seconds of clean speech from the target speaker as a WAV file.
2. Create a voice profile directory and config:

```bash
mkdir -p voices/my_voice
cp /path/to/recording.wav voices/my_voice/reference.wav
```

```json
// voices/my_voice/profile.json
{
  "name": "my_voice",
  "display_name": "My Custom Voice",
  "language": "English",
  "ref_audio": "reference.wav",
  "ref_text": "The exact words spoken in the reference audio.",
  "x_vector_only": false
}
```

3. Set `tts.backend` to `"local"` and `tts.default_voice` to `"my_voice"` in `config.json`.
4. Restart the server. The Base model loads automatically for clone profiles.

See `references/voice-cloning.md` for reference audio preparation, `x_vector_only` mode, prompt caching, and generation parameter tuning.

### Option B: Use a Preset Speaker

Set `tts.default_voice` to any preset name. No profile directory needed.

| Speaker | Language | Speaker | Language |
|---------|----------|---------|----------|
| Ryan | English | Vivian | Chinese |
| Aiden | English | Serena | Chinese |
| Ono_Anna | Japanese | Uncle_Fu | Chinese |
| Sohee | Korean | Dylan | Chinese |
| | | Eric | Chinese |

Preset speakers use the CustomVoice model and support instruction-based style control. See `references/voice-design.md` for details.

### Option C: Design a Voice from a Description

Use the VoiceDesign model to create voices from text descriptions like "Warm male voice, 30s, baritone, calm and reassuring." This requires the VoiceDesign model variant, which is not currently integrated into agent-native-speech's engine but can be used standalone. See `references/voice-design.md`.

## Integration Architecture

### File Map

| File | Role |
|------|------|
| `server/tts_engine.py` | `TTSEngine` -- loads Qwen3-TTS models, dispatches synthesis |
| `server/voice_profile.py` | `VoiceProfileRegistry` -- scans `voices/` dir, manages profiles |
| `server/tts_backend.py` | `TTSBackend` protocol, `preprocess()` text splitting |
| `server/config.py` | `TTSConfig` dataclass with backend, voice, device settings |

### How Models Are Loaded

The engine uses **mutual-exclusion model loading** -- only one model stays in VRAM at a time:

- **Preset voice requested** --> loads CustomVoice model, unloads Base if loaded
- **Clone voice requested** --> loads Base model, unloads CustomVoice if loaded

Models load lazily on first synthesis. The `warmup()` method pre-loads the default voice's model.

### Voice Clone Prompt Caching

Extracting a voice clone prompt from reference audio is expensive (~2-5s). The engine caches prompts at two levels:

1. **Memory cache**: `TTSEngine._prompt_cache` dict, keyed by profile name
2. **Disk cache**: `voices/<name>/prompt_cache.pt`, invalidated by a hash of `ref_audio_path + ref_text + x_vector_only`

The cache is created automatically on first synthesis and reused on subsequent calls and restarts.

### Generation Parameters

The engine uses tuned generation parameters that prioritize consistency over expressiveness:

| Parameter | Clone | Preset |
|-----------|-------|--------|
| temperature | 0.3 | 0.7 |
| top_k | 10 | 30 |
| top_p | 0.8 | 0.9 |
| repetition_penalty | 1.1 | 1.05 |

These are defined as `CLONE_GENERATE_KWARGS` and `PRESET_GENERATE_KWARGS` in `tts_engine.py`.

### Audio Post-Processing

All synthesized audio passes through:
1. **80 Hz high-pass filter** (4th-order Butterworth) -- removes low-frequency rumble
2. **RMS normalization** to -20 dBFS -- consistent volume across chunks
3. **Crossfade concatenation** (50ms overlap) -- eliminates seams between sentence chunks

Output format: float32 mono ndarray at 24 kHz. `audio_source.py` handles conversion to Discord's 48 kHz stereo int16 format.

## Environment Requirements

- Python 3.12, CUDA-capable GPU
- `pip install -e '.[tts]'` (installs `qwen-tts`)
- Optional: `pip install -U flash-attn --no-build-isolation` (lower VRAM, faster inference)
- Models download from HuggingFace on first load (~3.5GB for 1.7B, ~1.2GB for 0.6B)

## Workflow Overviews

### Creating a New Clone Voice

1. Prepare reference audio (clean WAV, 5-30s, single speaker)
2. Transcribe the reference audio accurately
3. Create `voices/<name>/profile.json` with name, ref_audio, ref_text
4. Place reference WAV alongside profile.json
5. Set as default or use in voice pool
6. Server auto-detects on restart; prompt cache builds on first synthesis

See `references/voice-cloning.md` for the complete workflow.

### Fine-Tuning for Better Quality

When voice cloning alone does not capture a speaker well enough, fine-tune the Base model on 10-100 audio samples from that speaker. This produces a checkpoint that can replace the HuggingFace model ID in the engine.

See `references/fine-tuning.md` for the community fine-tuning tool, dataset preparation, training, and inference.

### Using Streaming for Lower Latency

The official `qwen-tts` library does not support token-level streaming. A community fork adds `stream_generate_pcm()` and `stream_generate_voice_clone()` with approximately 6x performance improvement. See `references/fine-tuning.md` for details on the streaming fork.

## Detailed References

- `references/model-downloads.md` -- All model IDs, download commands, sizes, VRAM requirements
- `references/voice-cloning.md` -- Clone workflow, reference audio prep, prompt caching, profile.json format
- `references/voice-design.md` -- VoiceDesign model, CustomVoice speakers, instruction control
- `references/fine-tuning.md` -- Community fine-tuning tool, dataset prep, training, streaming fork
