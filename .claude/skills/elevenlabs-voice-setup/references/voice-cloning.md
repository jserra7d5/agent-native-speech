# Instant Voice Cloning (IVC)

## Overview

Instant Voice Cloning creates a digital replica of a voice from audio samples. No fine-tuning or training queue -- the voice is available immediately after upload. This is the fastest way to get a custom voice for use in agent-native-speech.

## Audio Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Duration | 30 seconds | 60+ seconds of continuous speech |
| Bitrate | 128kbps | 192kbps+ |
| Format | mp3, wav, m4a, ogg, flac, webm | wav (uncompressed) |
| Channels | mono or stereo | mono preferred |
| Sample rate | 16kHz+ | 44.1kHz or 48kHz |
| Background noise | Minimal | None (clean studio recording) |
| Content | Speech only | Natural, varied speech (not monotone reading) |

**Tips for best results:**
- Record in a quiet room with minimal echo
- Use a decent microphone (even a good headset works)
- Speak naturally with varied intonation -- avoid monotone reading
- Include a range of emotions and pacing if you want expressive output
- Avoid long pauses, coughs, or non-speech sounds
- Multiple shorter samples (30-60s each) can be better than one long noisy one

## Basic Voice Clone

### Single file

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

voice = client.voices.ivc.create(
    name="Agent Alpha",
    description="Professional male narrator, warm tone, American English",
    files=[open("recording.wav", "rb")],
)

print(f"Created voice: {voice.name}")
print(f"Voice ID: {voice.voice_id}")
```

### Multiple files

Providing multiple samples improves voice quality and consistency:

```python
sample_files = [
    open("sample_1.wav", "rb"),
    open("sample_2.wav", "rb"),
    open("sample_3.wav", "rb"),
]

voice = client.voices.ivc.create(
    name="Agent Alpha",
    description="Professional male narrator, warm tone",
    files=sample_files,
)

# Close files after upload
for f in sample_files:
    f.close()

print(f"Voice ID: {voice.voice_id}")
```

**Maximum files:** Up to 25 samples per voice.
**Maximum total size:** 50MB across all files.

## Adding the Voice to agent-native-speech

After creating the voice, add it to `config.json`:

```json
{
  "tts": {
    "elevenlabs": {
      "voices": {
        "AgentAlpha": "<voice_id>"
      }
    }
  },
  "voice_pool": ["AgentAlpha", "Ryan", "Aiden"]
}
```

To make it the default voice for single-session mode:

```json
{
  "tts": {
    "default_voice": "AgentAlpha",
    "elevenlabs": {
      "default_voice_id": "<voice_id>",
      "voices": {
        "AgentAlpha": "<voice_id>"
      }
    }
  }
}
```

## Testing the Cloned Voice

Quick test without running the full server:

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

# Generate test audio
audio = client.text_to_speech.convert(
    text="Hello, this is a test of my cloned voice.",
    voice_id="<voice_id>",
    model_id="eleven_flash_v2_5",
    output_format="mp3_44100_128",
)

# Save to file
with open("test_output.mp3", "wb") as f:
    for chunk in audio:
        f.write(chunk)

print("Saved test_output.mp3")
```

## Adding Samples to an Existing Voice

You can add more audio samples to improve an existing cloned voice:

```python
client.voices.edit(
    voice_id="<voice_id>",
    name="Agent Alpha",  # name is required even if unchanged
    files=[open("additional_sample.wav", "rb")],
)
```

Note: Editing a voice with new files replaces all previous samples. To add to existing samples, you need to re-upload all files (old + new).

## Labels and Organization

Add labels to organize voices for easy filtering:

```python
voice = client.voices.ivc.create(
    name="Agent Alpha",
    description="Professional male narrator",
    files=[open("sample.wav", "rb")],
    labels={"accent": "american", "gender": "male", "use_case": "agent"},
)
```

## Common Issues

| Issue | Cause | Fix |
|---|---|---|
| Voice sounds nothing like source | Poor audio quality or too short | Use 60s+ clean audio |
| Voice is monotone | Source recording was monotone | Re-record with natural varied intonation |
| Background noise in output | Noise in source samples | Clean source audio or re-record |
| 400 Bad Request | Invalid file format or too large | Check format and size limits |
| Voice quality degrades over time | Not an actual issue | Voice quality is deterministic per model version |

## IVC vs Professional Voice Cloning (PVC)

| Feature | IVC (Instant) | PVC (Professional) |
|---|---|---|
| Setup time | Seconds | Hours (fine-tuning) |
| Audio needed | 30s-5min | 30+ minutes |
| Quality | Good | Best possible |
| Availability | All paid plans | Enterprise only |
| Cost | Included in plan | Additional fee |
| Best for | Prototyping, personal use | Production, brand voices |

For most agent-native-speech use cases, IVC provides sufficient quality. Consider PVC only if you need the absolute highest fidelity for a production deployment.
