# TTS Pipeline Reference

## Text Preprocessing (`server/tts_backend.py`)

All TTS backends share the same preprocessing pipeline via `preprocess()`:

1. **Strip code blocks**: Fenced code blocks (` ```...``` `) are replaced with "code block omitted". Inline backticks are stripped, keeping the inner text.
2. **Normalize whitespace**: Collapse all runs of whitespace to single spaces.
3. **Sentence splitting**: Split on sentence-ending punctuation (`.!?`) followed by whitespace. Each sentence becomes a synthesis chunk.
4. **Long sentence fallback**: Sentences exceeding `MAX_CHUNK_CHARS` (500) are further split on clause-level punctuation (`,;:`).

The `preprocess()` function returns `list[str]` -- one string per chunk. An empty list means nothing to synthesize. CallManager uses the chunk count to decide streaming vs non-streaming path.

```python
# Regex patterns defined in tts_backend.py
_CODE_BLOCK_RE  # Fenced code blocks with optional language tag
_INLINE_CODE_RE # Inline code spans (`...`)
_SENTENCE_SPLIT_RE  # Split on .!? followed by whitespace
MAX_CHUNK_CHARS = 500
```

## TTSBackend Protocol

Defined in `server/tts_backend.py` as a `@runtime_checkable` Protocol:

```python
class TTSBackend(Protocol):
    @property
    def is_loaded(self) -> bool: ...
    def synthesize(self, text: str, voice: str | None = None) -> tuple[np.ndarray, int]: ...
    def synthesize_streamed(self, text: str, voice: str | None = None) -> Iterator[tuple[np.ndarray, int]]: ...
    def warmup(self) -> None: ...
    def unload(self) -> None: ...
```

Both `synthesize()` and `synthesize_streamed()` accept the raw text (preprocessing is done internally by each backend). The `voice` parameter is optional; backends fall back to their configured default.

## Local TTS Engine (`server/tts_engine.py`)

### Models

Two Qwen3-TTS model variants, mutually exclusive in VRAM:

| Model | HF ID | Use Case |
|---|---|---|
| CustomVoice | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | Preset speakers (Ryan, Aiden, etc.) |
| Base | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | Voice cloning from reference audio |

Loading one model type automatically unloads the other (`_get_model_for_profile()`). CUDA cache is cleared after each unload.

### Model Loading Details

- Prefers `bfloat16` on Ampere+ GPUs (RTX 30xx/40xx), falls back to `float16`
- Uses `flash_attention_2` when `flash-attn` is installed (reduces VRAM)
- `device_map` comes from `TTSConfig.device` (usually `"cuda"`)

### Voice Profiles

Managed by `VoiceProfileRegistry` in `server/voice_profile.py`:

**Preset voices** -- Built into the CustomVoice model:
- English: `Ryan`, `Aiden`
- Chinese: `Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric`
- Japanese: `Ono_Anna`
- Korean: `Sohee`

**Clone voices** -- Loaded from `voices/<name>/profile.json`:
```json
{
    "name": "my_voice",
    "display_name": "My Voice",
    "language": "English",
    "ref_audio": "reference.wav",
    "ref_text": "Transcript of the reference audio.",
    "x_vector_only": false
}
```

Clone profiles require:
- `reference.wav` in the same directory
- `ref_text` must be non-empty when `x_vector_only` is false
- Prompt extraction is cached to `prompt_cache.pt` (invalidated by hash of profile fields)

### Voice Clone Prompt Caching

Three-level cache for voice clone prompts (expensive to extract):
1. **Memory cache**: `TTSEngine._prompt_cache` dict (keyed by profile name)
2. **Disk cache**: `voices/<name>/prompt_cache.pt` (PyTorch serialized, validated by profile hash)
3. **Extract from model**: `model.create_voice_clone_prompt()` -- slow, only on cache miss

Cache invalidation uses a SHA-256 hash of `ref_audio_path|ref_text|x_vector_only`.

### Generation Parameters

```python
# Clone voices -- conservative for consistency
CLONE_GENERATE_KWARGS = {
    "temperature": 0.3, "top_k": 10, "top_p": 0.8,
    "repetition_penalty": 1.1,
    "subtalker_temperature": 0.3, "subtalker_top_k": 10, "subtalker_top_p": 0.8,
    "non_streaming_mode": True,
}

# Preset voices -- slightly more expressive
PRESET_GENERATE_KWARGS = {
    "temperature": 0.7, "top_k": 30, "top_p": 0.9,
    "repetition_penalty": 1.05,
    "subtalker_temperature": 0.7, "subtalker_top_k": 30, "subtalker_top_p": 0.9,
}
```

### Audio Post-Processing

Every synthesized chunk goes through `_post_process()`:
1. **High-pass filter**: 80 Hz cutoff, 4th-order Butterworth (scipy). Removes DC offset and low-frequency rumble.
2. **RMS normalization**: Target -20 dBFS. Segments quieter than -60 dBFS are left unchanged to avoid amplifying silence/noise.

### Crossfade Concatenation

When `synthesize()` produces multiple chunks, they are joined with a 50ms linear crossfade (`CROSSFADE_SAMPLES = 1200` at 24 kHz). This eliminates hard-cut seams between sentences. `synthesize_streamed()` does NOT crossfade -- each chunk is yielded independently for immediate playback.

### Output

Always `(float32 mono ndarray, 24000)`. The 24 kHz rate is the native Qwen3-TTS output.

## ElevenLabs TTS (`server/elevenlabs_tts.py`)

### Voice ID Resolution

`_resolve_voice_id()` resolution order:
1. `None` -> default voice ID from config
2. Name in `self._voices` map -> mapped ElevenLabs voice ID
3. Alphanumeric string 16+ chars -> passed through as raw voice ID
4. Unknown name -> warning + fallback to default

The `voices` map is populated from `tts.elevenlabs.voices` in config.json.

### Synthesis

- **Non-streaming**: `client.text_to_speech.convert()` with `output_format="pcm_24000"`. All chunks from `preprocess()` are joined into one string.
- **Streaming**: `client.text_to_speech.stream()`. Accumulates at least 0.5s of audio (48000 bytes at 16-bit 24kHz) before yielding to avoid tiny fragments.

Raw PCM bytes from the API (signed 16-bit LE) are converted to float32 via `_pcm_bytes_to_float32()`: `int16 / 32768.0`.

### Output

Always `(float32 mono ndarray, 24000)` -- matches the local engine's contract exactly. Downstream code (audio_source) requires zero changes.

## CallManager TTS Integration (`server/call_manager.py`)

### `_tts_speak()` Logic

```
message -> preprocess() -> count chunks
  |
  +-- 0-1 chunks: synthesize() full -> TTSAudioSource.from_audio() -> play()
  |                                     await source.done (threading.Event)
  |
  +-- 2+ chunks: StreamingAudioSource created
                  Background executor: synthesize_streamed() -> add_segment() per chunk -> finish()
                  Main: voice_client.play(source) -> await synth_future + source.done
```

Error handling: if playback fails with `ClientException`, `source.finish()` is called to clean up the streaming source, and `synth_future` is awaited to prevent orphan threads.
