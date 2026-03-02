# Voice Management

## Overview

The ElevenLabs API provides full CRUD operations for voices. This reference covers listing, inspecting, editing, deleting, and configuring voice settings.

## Authentication

All API calls require authentication:

```python
from elevenlabs import ElevenLabs

# Via constructor
client = ElevenLabs(api_key="sk_...")

# Or set the environment variable ELEVENLABS_API_KEY
# and instantiate without arguments:
client = ElevenLabs()
```

REST API uses the `xi-api-key` header:

```bash
curl -H "xi-api-key: sk_..." "https://api.elevenlabs.io/v1/voices"
```

## Listing Voices

### List all voices (SDK)

```python
response = client.voices.search()

for voice in response.voices:
    print(f"{voice.name}: {voice.voice_id}")
    print(f"  Category: {voice.category}")
    print(f"  Labels: {voice.labels}")
    print()
```

Voice categories:
- `premade` -- ElevenLabs stock voices
- `cloned` -- Your IVC/PVC voices
- `generated` -- Text-to-voice created voices

### Filter by category

```python
# The SDK returns all voices; filter in Python
cloned_voices = [v for v in response.voices if v.category == "cloned"]
```

### REST API

```bash
# List all voices
curl -H "xi-api-key: sk_..." "https://api.elevenlabs.io/v1/voices"
```

## Getting Voice Details

```python
voice = client.voices.get(voice_id="CYDzJWiIyIiQuhRB4r1K")

print(f"Name: {voice.name}")
print(f"ID: {voice.voice_id}")
print(f"Category: {voice.category}")
print(f"Description: {voice.description}")
print(f"Labels: {voice.labels}")
print(f"Preview URL: {voice.preview_url}")
print(f"Settings: {voice.settings}")
```

## Editing Voices

### Update name and description

```python
client.voices.edit(
    voice_id="<voice_id>",
    name="New Name",
    description="Updated description of this voice",
)
```

### Update labels

```python
client.voices.edit(
    voice_id="<voice_id>",
    name="Agent Alpha",  # name is required even if unchanged
    labels={"accent": "british", "gender": "male", "age": "middle-aged"},
)
```

### Replace audio samples (IVC voices only)

```python
client.voices.edit(
    voice_id="<voice_id>",
    name="Agent Alpha",
    files=[
        open("new_sample_1.wav", "rb"),
        open("new_sample_2.wav", "rb"),
    ],
)
```

**Warning:** Uploading new files replaces all previous samples. To add samples, re-upload everything (old + new).

### REST API

```bash
curl -X POST "https://api.elevenlabs.io/v1/voices/<voice_id>/edit" \
  -H "xi-api-key: sk_..." \
  -F "name=New Name" \
  -F "description=Updated description" \
  -F 'labels={"accent": "british"}'
```

## Deleting Voices

```python
client.voices.delete(voice_id="<voice_id>")
```

**Warning:** Deletion is permanent and immediate. There is no undo. Remove the voice from `config.json` before or after deletion to avoid runtime errors.

REST API:

```bash
curl -X DELETE -H "xi-api-key: sk_..." \
  "https://api.elevenlabs.io/v1/voices/<voice_id>"
```

## Voice Settings

Voice settings control the synthesis characteristics. They can be set per-request or as defaults on the voice.

### Settings parameters

| Setting | Range | Default | Description |
|---|---|---|---|
| `stability` | 0.0 - 1.0 | 0.5 | Higher = more consistent, lower = more expressive/variable |
| `similarity_boost` | 0.0 - 1.0 | 0.75 | Higher = closer to original voice, can amplify artifacts |
| `style` | 0.0 - 1.0 | 0.0 | Expressiveness exaggeration (increases latency) |
| `use_speaker_boost` | bool | true | Enhances speaker similarity (slight latency cost) |

### Applying settings per-request

```python
from elevenlabs import VoiceSettings

audio = client.text_to_speech.convert(
    text="Hello world",
    voice_id="<voice_id>",
    model_id="eleven_flash_v2_5",
    output_format="pcm_24000",
    voice_settings=VoiceSettings(
        stability=0.6,
        similarity_boost=0.8,
        style=0.0,
        use_speaker_boost=True,
    ),
)
```

### Recommended settings by use case

| Use case | stability | similarity_boost | style | speaker_boost |
|---|---|---|---|---|
| Consistent agent voice | 0.6-0.8 | 0.7-0.9 | 0.0 | True |
| Expressive narration | 0.3-0.5 | 0.6-0.8 | 0.3-0.5 | True |
| Maximum voice fidelity | 0.5 | 0.9-1.0 | 0.0 | True |
| Low latency (real-time) | 0.5 | 0.75 | 0.0 | False |

**Note:** The agent-native-speech `ElevenLabsTTSEngine` currently uses default voice settings. To customize, you would modify `elevenlabs_tts.py` to accept and pass `voice_settings` in the `synthesize()` and `synthesize_streamed()` methods.

## Useful Patterns for agent-native-speech

### Audit voice pool configuration

Verify all configured voice IDs are valid:

```python
import json
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

with open("config.json") as f:
    config = json.load(f)

configured_voices = config.get("tts", {}).get("elevenlabs", {}).get("voices", {})
api_voices = {v.voice_id: v.name for v in client.voices.search().voices}

for name, vid in configured_voices.items():
    if vid in api_voices:
        print(f"  OK: {name} -> {vid} ({api_voices[vid]})")
    else:
        print(f"  MISSING: {name} -> {vid} (not found in account)")
```

### Export voice list for config

Generate the `voices` dict for `config.json` from your ElevenLabs account:

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")
response = client.voices.search()

voices_dict = {}
for v in response.voices:
    # Use the voice name as the key, sanitized
    key = v.name.replace(" ", "")
    voices_dict[key] = v.voice_id

import json
print(json.dumps(voices_dict, indent=2))
```

### Check voice quota usage

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")
subscription = client.user.get_subscription()

print(f"Character limit: {subscription.character_limit}")
print(f"Characters used: {subscription.character_count}")
remaining = subscription.character_limit - subscription.character_count
print(f"Remaining: {remaining}")
print(f"Next reset: {subscription.next_character_count_reset_unix}")
```
