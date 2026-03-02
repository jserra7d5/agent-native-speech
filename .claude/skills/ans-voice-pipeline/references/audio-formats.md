# Audio Formats Reference

## Discord Audio Requirements

Discord voice uses a fixed format that cannot be changed:

| Parameter | Value |
|---|---|
| Sample rate | 48,000 Hz |
| Channels | 2 (stereo) |
| Sample width | 2 bytes (signed 16-bit little-endian) |
| Frame duration | 20 ms |
| Frame size | 3,840 bytes (48000 * 2 * 2 * 0.020) |
| Encoding | Raw PCM (not Opus -- `is_opus()` returns False) |

These constants are defined in both `audio_source.py` and `audio_sink.py`.

## Audio Source -- TTS to Discord (`server/audio_source.py`)

### TTSAudioSource

Converts TTS engine output to Discord-ready PCM. All conversion happens in the `from_audio()` factory method (constructor just stores pre-converted bytes).

**Conversion pipeline in `from_audio()`**:

```
float32 mono (any sample rate)
  |
  1. Ensure float32 dtype
  2. Collapse multi-channel to mono (average axis 0) if needed
  3. Resample to 48 kHz via librosa.resample() (skipped if already 48k)
  4. Mono -> stereo: np.stack([audio, audio], axis=1)
  5. Clip to [-1.0, 1.0]
  6. Scale to int16: multiply by 32767 (not 32768, for headroom)
  7. Serialize to little-endian bytes via .tobytes()
  |
  v
bytes (48kHz stereo int16 PCM)
```

**`read()` method** (called from Discord audio thread):
- Returns exactly 3,840 bytes per call
- Last partial frame is zero-padded to 3,840 bytes
- Returns `b""` when exhausted (signals discord.py to stop)
- Sets `threading.Event` (`done`) on last frame

**`from_file()` factory**: Loads any soundfile-readable format, transposes multi-channel to (channels, samples), delegates to `from_audio()`. Primarily for testing.

**Threading**: `done` is a `threading.Event`, not `asyncio.Event`, because `read()` is called from Discord's audio thread. CallManager polls it from the async loop via `await asyncio.sleep(0.05)` in a while loop.

### StreamingAudioSource

For multi-sentence TTS where synthesis and playback overlap.

**Producer interface** (synthesis background thread):
- `add_segment(audio, sample_rate)`: Converts via `TTSAudioSource.from_audio()`, extracts the `_data` bytes, enqueues in a `collections.deque`
- `finish()`: Signals no more segments are coming

**Consumer interface** (Discord audio thread via `read()`):
- Reads from current segment, advancing to next when exhausted
- Returns silence frame (`b"\x00" * 3840`) when buffer underrun (synthesis still running)
- Returns `b""` when all segments consumed AND `finish()` was called

**Thread safety**: All state access protected by `threading.Lock`. The deque, current segment, offset, and finished flag are all guarded.

**Cleanup**: `cleanup()` clears all buffers, sets finished flag, and signals `done`. Called by discord.py on voice client stop/disconnect.

## Audio Sink -- Discord to STT (`server/audio_sink.py`)

### UserAudioSink

Captures per-user audio from Discord and converts it for downstream consumption.

**Conversion pipeline in `write()`**:

```
48kHz stereo int16 PCM bytes (3840 bytes, 20ms frame)
  |
  1. np.frombuffer(pcm_bytes, dtype=np.int16)
  2. Reshape to (-1, 2) -- interleaved stereo
  3. Cast to float32
  4. Average channels: stereo.mean(axis=1) -> mono
  5. Normalize: divide by 32768.0 -> [-1.0, 1.0]
  6. Resample 48kHz -> 16kHz via librosa.resample(res_type="soxr_hq")
  |
  v
float32 mono 16kHz numpy array
```

**Ring buffer**: Chunks accumulate in `_chunks` list with `_total_samples` tracking. When total exceeds `max_duration_s * 16000` (default 30s), oldest chunks are evicted from the front.

**`get_audio()`**: Returns concatenated buffer as single ndarray, clears the buffer and resets `_receiving` flag. Returns `None` if empty.

**Thread safety**: `write()` is called from Discord's voice-receive thread. `get_audio()`, `reset()`, `is_receiving()` can be called from any thread. All access protected by `threading.Lock`.

**User filtering**: Only frames from `self.member` are processed; all others are silently dropped.

### MultiUserAudioSink

Creates `UserAudioSink` instances lazily on first frame from each user. Useful for future multi-user scenarios, but currently the STT pipeline uses `UserAudioSink` directly with a known target user.

## Sample Rate Summary

```
Discord (48kHz stereo int16)
    |                    ^
    v                    |
AudioSink (48k->16k)    AudioSource (Nk->48k, mono->stereo, float->int16)
    |                    ^
    v                    |
VAD/Whisper (16kHz)     TTS Engine (24kHz mono float32)
```

Key resampling operations:
- **AudioSink**: 48kHz -> 16kHz using `librosa.resample(res_type="soxr_hq")`
- **AudioSource**: Any rate -> 48kHz using `librosa.resample()` (default resampler)

## Threading Model

The voice pipeline spans three thread contexts:

| Thread | Operations | Sync Mechanism |
|---|---|---|
| Main asyncio loop | `_tts_speak()`, `_stt_listen()`, `_wait_for_speech()` polling | `asyncio.sleep()` polling |
| Discord audio thread | `TTSAudioSource.read()`, `UserAudioSink.write()` | `threading.Lock`, `threading.Event` |
| TTS synthesis executor | `synthesize()`, `synthesize_streamed()` | `loop.run_in_executor()`, `StreamingAudioSource._lock` |

**Key constraint**: `read()` and `write()` methods on audio sources/sinks must be fast and non-blocking -- they are called from Discord's internal threads at a fixed 20ms cadence. Any blocking would cause audio glitches.

**CallManager threading bridge**: CallManager runs in the MCP server's async event loop. Discord bot runs in a separate thread with its own event loop. Voice operations are dispatched via `BotRunner.run_coroutine()` (blocking) or `BotRunner.run_coroutine_async()` (non-blocking future). The audio thread is internal to discord.py and cannot be directly controlled.

## Numerical Constants

```python
# audio_source.py
DISCORD_SAMPLE_RATE = 48_000
DISCORD_CHANNELS = 2
DISCORD_SAMPLE_WIDTH = 2  # bytes (int16)
DISCORD_FRAME_MS = 0.020  # 20ms
DISCORD_FRAME_BYTES = 3840
TTS_SAMPLE_RATE = 24_000

# audio_sink.py
OUTPUT_SAMPLE_RATE = 16_000  # for Whisper/VAD

# vad.py
_SILERO_WINDOW_SAMPLES = 512  # 32ms at 16kHz
_PRE_SPEECH_PAD_MS = 300
_PRE_SPEECH_PAD_SAMPLES = 4800

# tts_engine.py
OUTPUT_SAMPLE_RATE = 24_000
CROSSFADE_SAMPLES = 1200  # 50ms at 24kHz

# stt_pipeline.py
_POLL_INTERVAL_S = 0.05  # 50ms
_SILENCE_INJECT_DELAY_S = 0.3  # 300ms

# transcriber.py
MAX_PROMPT_TOKENS = 224
CHARS_PER_TOKEN = 4.0
MIN_AUDIO_DURATION_S = 0.1
SAMPLE_RATE = 16_000
```
