# Voice Design (Text-to-Voice)

## Overview

Voice Design creates a completely new synthetic voice from a natural language description -- no audio samples needed. This is useful when you want a specific vocal character but do not have a reference recording.

## How It Works

1. You provide a text description of the desired voice characteristics
2. You provide a sample text for the voice to speak (used as a preview)
3. ElevenLabs generates a new voice matching the description
4. The voice is saved to your account and can be used like any other voice

## Basic Usage

### Python SDK

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

voice = client.text_to_voice.create(
    voice_description="A warm, deep-voiced British man in his 40s. He speaks slowly and clearly with a calm, reassuring tone. Slight London accent.",
    text="Hello there. I'm your AI assistant, and I'm here to help you with whatever you need today.",
)

print(f"Voice name: {voice.name}")
print(f"Voice ID: {voice.voice_id}")
```

### REST API

```bash
curl -X POST "https://api.elevenlabs.io/v1/text-to-voice/create" \
  -H "xi-api-key: sk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "voice_description": "A warm, deep-voiced British man in his 40s.",
    "text": "Hello there. I am your AI assistant."
  }'
```

Response:

```json
{
  "voice_id": "...",
  "name": "Generated Voice ...",
  "preview_url": "https://..."
}
```

## Writing Effective Voice Descriptions

The quality of the generated voice depends heavily on the description. Be specific and descriptive.

### Good descriptions include:

- **Gender and age:** "A young woman in her mid-20s" or "An older man, around 60"
- **Vocal quality:** "deep", "bright", "raspy", "smooth", "nasal", "breathy"
- **Accent/dialect:** "American Midwest accent", "slight Irish lilt", "neutral BBC English"
- **Speaking style:** "speaks quickly and energetically", "slow and deliberate", "conversational"
- **Emotional tone:** "warm and friendly", "authoritative and confident", "calm and soothing"
- **Character archetype:** "like a news anchor", "like a friendly librarian"

### Examples of effective descriptions

**Professional assistant:**
> "A professional American woman in her 30s with a clear, confident voice. She speaks at a moderate pace with precise enunciation. Her tone is warm but businesslike, like a senior executive giving a presentation."

**Friendly companion:**
> "A cheerful young man in his early 20s with an energetic, upbeat voice. He has a slight California accent and speaks quickly with enthusiasm. His tone is casual and friendly, like he's talking to his best friend."

**Calm narrator:**
> "A middle-aged British man with a deep, resonant voice. He speaks slowly and deliberately, with perfect diction. His tone is calm and authoritative, like a documentary narrator."

**Avoid vague descriptions like:**
- "A nice voice" (too generic)
- "Sounds good" (no characteristics)
- "Normal person talking" (not descriptive enough)

## Previewing Before Saving

The `text` parameter in the creation call serves as the preview text. Choose text that:

- Is 1-3 sentences long
- Represents the kind of content the voice will speak
- Includes varied punctuation (questions, exclamations) to test expressiveness
- Avoids technical jargon or unusual words for the initial test

Good preview texts:

```
"Hello! Welcome to our service. I'm here to help you with anything you need. How are you doing today?"
```

```
"The weather forecast shows clear skies for the rest of the week. Temperatures will range from sixty-five to seventy-eight degrees."
```

## Integration with agent-native-speech

After generating a voice, add it to your config:

```json
{
  "tts": {
    "elevenlabs": {
      "voices": {
        "DesignedVoice": "<generated_voice_id>"
      }
    }
  },
  "voice_pool": ["DesignedVoice", "Ryan"]
}
```

## Generating Multiple Candidates

The API is not deterministic -- each call produces a different voice even with the same description. Generate several candidates and pick the best one:

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

description = "A calm, mature woman with a smooth alto voice and neutral American accent."
preview_text = "Hello, I'm your assistant. Let me know how I can help."

candidates = []
for i in range(3):
    voice = client.text_to_voice.create(
        voice_description=description,
        text=preview_text,
    )
    candidates.append(voice)
    print(f"Candidate {i+1}: {voice.voice_id} - {voice.name}")

# Listen to each candidate's preview, then delete the ones you don't want:
# client.voices.delete(voice_id="...")
```

**Note:** Each generation counts against your character quota (for the preview text synthesis).

## Limitations

- Generated voices may not perfectly match every aspect of the description
- Less control over specific vocal characteristics compared to IVC
- Quality depends heavily on the description quality
- No way to iteratively refine -- each call generates a new voice
- Generated voices cannot be further trained or fine-tuned
- The voice name is auto-generated; rename it via the voice edit API after creation

## Renaming a Generated Voice

```python
client.voices.edit(
    voice_id="<voice_id>",
    name="My Custom Agent Voice",
)
```

## When to Use Voice Design vs IVC

| Scenario | Recommendation |
|---|---|
| You have a recording of the target voice | IVC |
| You want a specific real person's voice | IVC |
| You want a new fictional voice | Voice Design |
| You need a specific accent/style combo | Voice Design (try several) |
| You need production-quality consistency | IVC with good samples |
| Quick prototyping, exploring options | Voice Design |
