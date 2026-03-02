# STT Pipeline Reference

## Pipeline Overview (`server/stt_pipeline.py`)

`STTPipeline` is the single entry point for all speech-to-text. Created once at server startup, it owns the VAD, transcriber, and correction manager.

### Backend Selection

Determined at construction by `config.stt.backend`:
- `"local"` -> `Transcriber` (faster-whisper, GPU)
- `"elevenlabs"` -> `ElevenLabsTranscriber` (cloud API, no GPU)

### `listen()` Method

Entry point called by `CallManager._stt_listen()`. Dispatches to one of two internal paths based on speech mode:

- **Pause mode** (`_listen_single`): Attach sink, wait for one complete utterance (VAD start -> VAD end), transcribe, correct, return.
- **Stop token mode** (`_listen_stop_token`): Loop of single-segment listens. Each segment is transcribed and checked for the stop word. Segments accumulate until stop word is found or `max_timeout_s` elapses.

### `_wait_for_speech()` -- The Core Poll Loop

Runs as an async loop polling at 50ms intervals (`_POLL_INTERVAL_S = 0.05`):

1. Drain `UserAudioSink.get_audio()` -- returns accumulated 16kHz mono float32 chunks
2. Feed audio into `SpeechDetector.process_chunk()`
3. If `SpeechEvent(type="end")` is returned, the audio buffer is ready for transcription
4. If no audio arrives for 300ms+ (`_SILENCE_INJECT_DELAY_S`), inject synthetic silence chunks into the VAD to trigger end-of-speech

The silence injection is critical: Discord stops sending audio packets when the user stops speaking. Without injection, the VAD's silence counter never advances and the utterance never ends.

### Debug Audio

After each `_wait_for_speech()` call, all received audio chunks are concatenated and saved to `/tmp/voice-agent-debug.wav`. This happens in the `finally` block so it works even on timeout or cancellation.

## VAD (`server/vad.py`)

### Silero VAD Model

- Loaded once at construction via `torch.hub.load("snakers4/silero-vad", "silero_vad")`
- Runs on CPU (lightweight, no GPU needed)
- Requires exactly 512 samples per inference call at 16 kHz (32ms window)
- Thread-safe: `process_chunk()` and `reset()` protected by a lock

### State Machine

Two states: `IDLE` and `SPEAKING`

```
IDLE: audio -> pre-buffer ring
      prob >= threshold -> SPEAKING (emit SpeechEvent("start"))
                           seed speech buffer with pre-buffer contents

SPEAKING: audio -> speech buffer (every window, regardless of VAD result)
          prob < threshold -> increment silence counter
          prob >= threshold -> reset silence counter
          silence counter >= threshold -> IDLE (emit SpeechEvent("end", audio=buffer))
```

### Pre-Speech Padding

A ring buffer holds the most recent 300ms of audio (`_PRE_SPEECH_PAD_SAMPLES = 4800` at 16kHz). When speech starts, this buffer is prepended to the speech audio so the leading edge of words is not clipped. The ring buffer evicts oldest windows when it exceeds the pad limit.

### Silence Duration Calculation

```python
frames_per_second = 16000 / 512  # = 31.25
silence_frames = (silence_duration_ms / 1000) * frames_per_second
# Default: (1500 / 1000) * 31.25 = 46.875 -> 46 frames
```

### Incoming Chunk Handling

Chunks of any size are accepted. Internally:
1. Prepend any leftover samples from previous call (`_remainder`)
2. Slice into 512-sample windows
3. Save trailing sub-window samples as new `_remainder`
4. Process each window through the model

### Configuration

```python
VADConfig:
    threshold: float = 0.5      # VAD probability threshold (0-1, lower = more sensitive)
    silence_duration_ms: int = 1500  # How long silence must persist to end speech
```

## Transcription

### Local Transcriber (`server/transcriber.py`)

**Model**: faster-whisper (CTranslate2-based Whisper). Loaded lazily on first `transcribe()` call.

**Key parameters passed to model.transcribe()**:
- `language="en"` -- hardcoded English
- `beam_size=5` -- balanced accuracy/speed
- `vad_filter=False` -- upstream VAD already handles this
- `no_speech_threshold=None` -- let upstream VAD decide
- `initial_prompt` -- vocabulary bias string

**Minimum audio duration**: 0.1 seconds. Shorter audio returns empty `TranscriptionResult`.

**GPU OOM handling**: Catches `OutOfMemoryError`, unloads model, clears CUDA cache, raises `RuntimeError`. Next call will reload the model.

### Initial Prompt Construction

`Transcriber.build_initial_prompt()` creates a vocabulary bias string within Whisper's 224-token budget (~896 characters at ~4 chars/token):

1. Collect correction dictionary values (canonical spellings)
2. Collect custom vocabulary terms
3. Pack terms greedily from the end (later = higher influence on decoder)
4. Join with ", "
5. Drop lowest-priority terms (earliest in list) if budget is exceeded

### ElevenLabs Transcriber (`server/elevenlabs_stt.py`)

Drop-in replacement for the local Transcriber. Same interface.

- Uses `client.speech_to_text.convert()` with the Scribe API
- Converts float32 audio to 16-bit PCM bytes for upload
- `build_initial_prompt()` returns empty string (Scribe does not support prompt biasing)
- `is_loaded` always returns True (no local model)
- Errors return empty `TranscriptionResult` rather than raising (graceful degradation)

### TranscriptionResult

```python
@dataclass(frozen=True)
class TranscriptionResult:
    text: str                    # Full transcript
    language: str                # BCP-47 code (e.g. "en")
    language_probability: float  # Confidence [0, 1]
    duration_s: float            # Audio duration in seconds
```

## Correction System (`server/correction.py`)

### Per-User Dictionaries

Stored as JSON files: `data/corrections/{user_id}.json`
- Flat object: `{"misheard_word": "correct_word", ...}`
- `"default"` user ID for server-wide corrections
- Lazy loaded, in-memory cached, persisted on every mutation

### LLM Correction Flow

```
raw transcript
  |
  +-- No corrections for user? -> return unchanged
  |
  +-- Pre-filter: any correction key substring in transcript?
  |     No -> return unchanged (skip API call)
  |
  +-- Build system prompt with correction rules
  +-- Call LLM (Claude Haiku or OpenAI-compatible)
  +-- Return corrected text
  |
  +-- On any error -> return original transcript (graceful degradation)
```

### LLM Backend Selection

CorrectionManager supports multiple LLM backends:

1. **Anthropic native** (`anthropic.AsyncAnthropic`): Used when `anthropic_api_key` is set
2. **OpenRouter**: `llm.backend = "openrouter"`, uses `llm.api_key` as Bearer token
3. **Codex OAuth**: `llm.backend = "codex_oauth"`, reads token from `~/.codex/auth.json`
4. **OpenAI-compatible**: `llm.backend = "openai_compatible"`, generic chat completions endpoint

All use the same system prompt format. Model is resolved: per-tool override (`correction.model`) -> shared LLM config (`llm.model`) -> hardcoded `"claude-haiku-4-5-20251001"`.

### System Prompt Structure

```
You are a speech-to-text correction assistant. The following transcript
may contain misrecognized words. Apply these known corrections:

- "whisper" -> "Whisper"
- "gpt4" -> "GPT-4"

Rules:
- Only fix words/phrases that match the known corrections
- Apply corrections case-insensitively
- Preserve all other text exactly as-is
- Return ONLY the corrected transcript, nothing else
```

## Speech Modes (`server/speech_mode.py`)

### Pause Mode (Default)

Standard VAD-based: silence triggers end of utterance. Good for conversational back-and-forth.

### Stop Token Mode

User says a keyword (default: "over") to signal they are done. Useful when the user needs to speak for longer than the silence threshold allows.

**Stop word detection** (`check_stop_word()`):
- Case-insensitive comparison
- Trailing punctuation stripped before checking (`.`, `,`, `!`, `?`, `;`, `:`)
- Returns `(found: bool, cleaned_transcript: str)` where cleaned has the stop word removed

**Accumulation loop** in `_listen_stop_token()`:
1. Listen for one VAD segment
2. Transcribe and correct it
3. Check if transcript ends with stop word
4. If yes: strip stop word, add to accumulated, return joined
5. If no: add to accumulated, loop
6. On timeout or no-speech: return whatever has accumulated

Segments are joined with spaces: `" ".join(accumulated_transcripts)`.

### Mode Switching

`SpeechModeManager.set_mode(mode, stop_word)` -- validates mode is "pause" or "stop_token". The manager is passed to `STTPipeline.listen()` which checks `speech_mode.is_stop_token()` to choose the code path.
