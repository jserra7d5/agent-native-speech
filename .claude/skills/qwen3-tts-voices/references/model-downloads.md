# Qwen3-TTS Model Downloads

## Model Variants

### Base Models (Voice Cloning)

Base models take a reference audio clip and clone the speaker's voice. They do not support instruction-based style control.

| Model | HuggingFace ID | Size | VRAM |
|-------|---------------|------|------|
| 1.7B-Base | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | ~3.5GB | ~6-8GB |
| 0.6B-Base | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` | ~1.2GB | ~3-4GB |

### CustomVoice Models (Preset Speakers)

CustomVoice models provide 9 built-in speakers with instruction-based emotion and style control. They do not support voice cloning.

| Model | HuggingFace ID | Size | VRAM |
|-------|---------------|------|------|
| 1.7B-CustomVoice | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | ~3.5GB | ~6-8GB |
| 0.6B-CustomVoice | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | ~1.2GB | ~3-4GB |

### VoiceDesign Model (Text-Described Voices)

Design entirely new voices from text descriptions (e.g., "Young female, energetic, slightly raspy"). Does not support voice cloning or preset speakers.

| Model | HuggingFace ID | Size | VRAM |
|-------|---------------|------|------|
| 1.7B-VoiceDesign | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | ~3.5GB | ~6-8GB |

### Tokenizer (Required by All Models)

All Qwen3-TTS models depend on this tokenizer. The `qwen-tts` Python package downloads it automatically, but you can pre-download it for offline use.

| Component | HuggingFace ID | Size |
|-----------|---------------|------|
| Tokenizer | `Qwen/Qwen3-TTS-Tokenizer-12Hz` | ~2GB |

## Download Commands

### Prerequisites

```bash
pip install -U "huggingface_hub[cli]"
# Optional: log in for gated models or faster downloads
huggingface-cli login
```

### Download Individual Models

```bash
# Tokenizer (required by all models, auto-downloaded by qwen-tts but can be pre-cached)
huggingface-cli download Qwen/Qwen3-TTS-Tokenizer-12Hz --local-dir ./Qwen3-TTS-Tokenizer-12Hz

# 1.7B-Base (voice cloning -- default for agent-native-speech clone profiles)
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-Base --local-dir ./Qwen3-TTS-12Hz-1.7B-Base

# 1.7B-CustomVoice (preset speakers -- default for agent-native-speech presets)
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --local-dir ./Qwen3-TTS-12Hz-1.7B-CustomVoice

# 1.7B-VoiceDesign (text-described voice creation)
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign --local-dir ./Qwen3-TTS-12Hz-1.7B-VoiceDesign

# 0.6B-Base (lightweight voice cloning)
huggingface-cli download Qwen/Qwen3-TTS-12Hz-0.6B-Base --local-dir ./Qwen3-TTS-12Hz-0.6B-Base

# 0.6B-CustomVoice (lightweight preset speakers)
huggingface-cli download Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice --local-dir ./Qwen3-TTS-12Hz-0.6B-CustomVoice
```

### Download with Resume (Large Models)

If a download is interrupted, re-running the same command automatically resumes:

```bash
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-Base --local-dir ./Qwen3-TTS-12Hz-1.7B-Base --resume-download
```

### Using a Local Model Path

Once downloaded, you can point agent-native-speech at a local path instead of a HuggingFace ID. The `Qwen3TTSModel.from_pretrained()` call accepts both HuggingFace IDs and local directory paths:

```python
# HuggingFace ID (downloads on first use, cached in ~/.cache/huggingface/)
model = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base", ...)

# Local path (no download needed)
model = Qwen3TTSModel.from_pretrained("./Qwen3-TTS-12Hz-1.7B-Base", ...)
```

To use a local path in agent-native-speech, modify the model ID constants in `server/tts_engine.py`:

```python
# In server/tts_engine.py
CUSTOM_VOICE_MODEL_ID: str = "/path/to/Qwen3-TTS-12Hz-1.7B-CustomVoice"
BASE_MODEL_ID: str = "/path/to/Qwen3-TTS-12Hz-1.7B-Base"
```

Or, if `tts.model_name` is configured in `config.json`, that value is used instead of the hardcoded defaults.

## HuggingFace Cache Behavior

By default, `from_pretrained()` downloads models to `~/.cache/huggingface/hub/`. Models are stored as symlinks to blob files. To change the cache location:

```bash
export HF_HOME=/path/to/custom/cache
# or
export HUGGINGFACE_HUB_CACHE=/path/to/custom/cache
```

## Disk Space Summary

| Configuration | Total Disk |
|--------------|-----------|
| Clone only (1.7B-Base + tokenizer) | ~5.5GB |
| Presets only (1.7B-CustomVoice + tokenizer) | ~5.5GB |
| Both clone + presets (1.7B variants) | ~9GB |
| Full set (all 1.7B variants + tokenizer) | ~12.5GB |
| Lightweight clone (0.6B-Base + tokenizer) | ~3.2GB |

## GPU VRAM Requirements

VRAM usage depends on the model size and attention implementation:

| Model Size | Without flash-attn | With flash-attn |
|-----------|-------------------|-----------------|
| 0.6B | ~3-4GB | ~2-3GB |
| 1.7B | ~6-8GB | ~4-6GB |

The engine uses bfloat16 on Ampere+ GPUs (RTX 30xx/40xx) and falls back to float16 otherwise. Both halve VRAM compared to float32.

Install flash-attn for reduced VRAM and faster inference:

```bash
pip install -U flash-attn --no-build-isolation
```

The engine auto-detects flash-attn and uses it when available. No configuration needed.

## Python Package

The `qwen-tts` package handles model loading and inference:

```bash
pip install -U qwen-tts
# or via agent-native-speech extras:
pip install -e '.[tts]'
```

This installs the `Qwen3TTSModel` class and its dependencies (transformers, torch, etc.). Models download from HuggingFace on first `from_pretrained()` call.
