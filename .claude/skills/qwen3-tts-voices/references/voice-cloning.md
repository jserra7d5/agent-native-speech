# Voice Cloning

Voice cloning uses a Base model (1.7B-Base or 0.6B-Base) to reproduce a speaker's voice from a short reference audio clip. This is the primary method for creating custom voices in agent-native-speech.

## Reference Audio Preparation

The quality of the cloned voice depends heavily on the reference audio. Follow these guidelines:

### Requirements

- **Format**: WAV (PCM), mono or stereo, any sample rate (will be resampled internally)
- **Duration**: 5-30 seconds. Shorter clips may lack speaker characteristics; longer clips increase prompt extraction time without proportional quality gain.
- **Content**: Natural speech at a normal pace. Avoid whispering, shouting, or singing.
- **Quality**: Clean recording with minimal background noise, no music, no other speakers.
- **Transcript**: You must provide the exact text spoken in the reference audio as `ref_text`.

### Tips for Best Results

- Use a high-quality microphone in a quiet room
- Avoid recordings with reverb or echo
- Include varied intonation (not monotone reading)
- The reference text should be conversational, not a list of words
- If possible, record the speaker saying something similar in style to what the TTS will produce
- Trim silence from the beginning and end of the recording

### Audio Processing

If your reference audio needs cleanup:

```bash
# Convert to mono WAV at 24kHz (matches Qwen3-TTS native rate)
ffmpeg -i input.mp3 -ac 1 -ar 24000 reference.wav

# Trim silence from start/end
ffmpeg -i input.wav -af "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,areverse" reference.wav

# Normalize volume
ffmpeg -i input.wav -af loudnorm reference.wav
```

## Voice Profile Configuration

### profile.json Format

Each voice clone lives in `voices/<name>/` with a `profile.json` and a reference WAV file.

```json
{
  "name": "alice",
  "display_name": "Alice (Customer Support)",
  "language": "English",
  "ref_audio": "reference.wav",
  "ref_text": "Hello, thank you for calling. How can I help you today? I'd be happy to assist with any questions you might have.",
  "x_vector_only": false
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Internal identifier. Must be unique across all profiles. Used in config and API calls. |
| `display_name` | string | No | Human-readable name. Defaults to `name`. |
| `language` | string | No | Primary language. Defaults to `"English"`. Must be one of the 10 supported languages. |
| `ref_audio` | string | Yes | Path to reference WAV, relative to the profile directory. |
| `ref_text` | string | Yes* | Exact transcript of the reference audio. Required when `x_vector_only` is false. |
| `x_vector_only` | bool | No | If true, only extract speaker embedding (x-vector) without full prompt. Faster but lower quality. Defaults to false. |

*`ref_text` can be empty only when `x_vector_only` is true.

### Directory Layout

```
voices/
  alice/
    profile.json
    reference.wav
    prompt_cache.pt      # auto-generated, do not commit
  bob/
    profile.json
    reference.wav
    prompt_cache.pt
```

### x_vector_only Mode

When `x_vector_only` is true, the engine extracts only the speaker's x-vector embedding from the reference audio, without encoding the full audio-text alignment. This is:

- **Faster**: Prompt extraction takes ~0.5s instead of ~2-5s
- **Lower quality**: Captures vocal timbre but may miss speaking style, rhythm, and prosody
- **No ref_text needed**: The transcript of the reference audio is not required

Use `x_vector_only` when you need a quick approximation or when you cannot obtain an accurate transcript of the reference audio.

## Standalone Voice Cloning API

### Basic Cloning

```python
import torch
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

# Single generation with inline reference
wavs, sr = model.generate_voice_clone(
    text="Hello, how are you today?",
    language="English",
    ref_audio="voices/alice/reference.wav",
    ref_text="Hello, thank you for calling. How can I help you today?",
)

# wavs is a list of numpy arrays (one per input text)
# sr is the sample rate (24000)
```

### Reusable Voice Clone Prompt

Extracting the voice clone prompt is the expensive step. Create it once and reuse for multiple generations:

```python
# Extract prompt once (~2-5 seconds)
prompt = model.create_voice_clone_prompt(
    ref_audio="voices/alice/reference.wav",
    ref_text="Hello, thank you for calling. How can I help you today?",
)

# Generate multiple utterances with the same voice (fast, ~0.5-2s each)
wavs, sr = model.generate_voice_clone(
    text="Good morning!",
    language="English",
    voice_clone_prompt=prompt,
)

wavs2, sr2 = model.generate_voice_clone(
    text="How can I help you?",
    language="English",
    voice_clone_prompt=prompt,
)
```

### Batch Generation

Pass lists for `text` and `language` to generate multiple utterances in a single call:

```python
wavs, sr = model.generate_voice_clone(
    text=["Good morning!", "How can I help you?", "Goodbye!"],
    language=["English", "English", "English"],
    voice_clone_prompt=prompt,
)
# wavs[0], wavs[1], wavs[2] are separate audio arrays
```

### x-Vector Only Mode

```python
prompt = model.create_voice_clone_prompt(
    ref_audio="voices/alice/reference.wav",
    ref_text="",  # not needed
    x_vector_only_mode=True,
)

wavs, sr = model.generate_voice_clone(
    text="Hello!",
    language="English",
    voice_clone_prompt=prompt,
)
```

## How agent-native-speech Uses Cloning

### Prompt Cache System

The `TTSEngine` in `server/tts_engine.py` implements a two-level cache for voice clone prompts:

1. **Memory cache** (`TTSEngine._prompt_cache`): Dict keyed by profile name. Lost on server restart.
2. **Disk cache** (`voices/<name>/prompt_cache.pt`): PyTorch serialized prompt. Persists across restarts. Invalidated by a SHA-256 hash of `ref_audio_path | ref_text | x_vector_only`.

Cache flow on synthesis:
```
synthesize(text, voice="alice")
  --> _resolve_profile("alice") --> VoiceProfile(type="clone")
  --> _get_model_for_profile() --> load Base model if needed
  --> _get_or_create_prompt()
      --> check memory cache --> hit? return
      --> check disk cache (prompt_cache.pt) --> valid hash? load & return
      --> extract from model --> save to memory + disk --> return
  --> model.generate_voice_clone(text, voice_clone_prompt=prompt)
```

### Generation Parameters

The engine uses conservative generation parameters for cloned voices to prioritize consistency:

```python
CLONE_GENERATE_KWARGS = {
    "temperature": 0.3,        # Low for consistent output
    "top_k": 10,               # Narrow sampling
    "top_p": 0.8,              # Nucleus sampling cutoff
    "repetition_penalty": 1.1, # Prevent repetitive artifacts
    "subtalker_temperature": 0.3,
    "subtalker_top_k": 10,
    "subtalker_top_p": 0.8,
    "non_streaming_mode": True,
}
```

To adjust these, edit `CLONE_GENERATE_KWARGS` in `server/tts_engine.py`. Higher temperature (0.5-0.9) gives more expressive but less stable output. Lower top_k (5) gives more consistent but potentially more robotic output.

### Using a Clone Voice in the Voice Pool

Clone voices can be assigned to agent sessions via the voice pool:

```json
// config.json
{
  "tts": {
    "backend": "local",
    "default_voice": "alice"
  },
  "voice_pool": ["alice", "bob", "Ryan"]
}
```

Clone voices and preset voices can be mixed in the pool. Each concurrent session gets a unique voice from the pool.

## Troubleshooting

### "ref_text is empty (required when x_vector_only is false)"

The `VoiceProfileRegistry` skips profiles where `x_vector_only` is false and `ref_text` is empty. Either provide the transcript or set `x_vector_only` to true.

### Poor voice quality

- Ensure `ref_text` exactly matches what is spoken in the reference audio
- Try a longer reference clip (15-30 seconds)
- Ensure reference audio is clean (no background noise, reverb, or music)
- Try lowering `temperature` in `CLONE_GENERATE_KWARGS` for more stable output
- Consider fine-tuning if cloning alone is insufficient (see `fine-tuning.md`)

### Slow first synthesis

The first synthesis with a new clone voice takes 5-15 seconds because it must:
1. Load the Base model (~3-5s)
2. Extract the voice clone prompt (~2-5s)
3. Generate audio (~1-3s)

Subsequent syntheses reuse the cached prompt and skip step 2. Use `TTSEngine.warmup()` to pre-load during server startup.

### Prompt cache invalidation

If you change the reference audio or ref_text, delete the `prompt_cache.pt` file to force re-extraction:

```bash
rm voices/alice/prompt_cache.pt
```

The engine will regenerate the cache on next synthesis. The hash-based invalidation should handle this automatically, but manual deletion is a safe fallback.
