# Voice Design and CustomVoice Speakers

Two model variants allow creating or controlling voices without reference audio: the **VoiceDesign** model (design new voices from text descriptions) and the **CustomVoice** model (use preset speakers with instruction-based style control).

## VoiceDesign Model

The VoiceDesign model generates speech in a voice described by a free-text instruction. No reference audio is needed -- the model creates a voice from scratch based on your description.

### Model ID

```
Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
```

### API Usage

```python
import torch
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

wavs, sr = model.generate_voice_design(
    text="Welcome to our service. How may I assist you today?",
    language="English",
    instruct="Warm male voice, early 30s, baritone, calm and professional tone",
)
# wavs[0] is a numpy array, sr is 24000
```

### Instruction Examples

The `instruct` parameter accepts free-text descriptions of the desired voice. Effective descriptions include:

**Demographic characteristics**:
- "Young female voice, early 20s, soprano"
- "Elderly male, deep gravelly voice, 70s"
- "Middle-aged woman, alto, clear enunciation"

**Emotional tone**:
- "Cheerful and energetic, slightly breathless with excitement"
- "Calm, measured, authoritative, like a news anchor"
- "Warm and gentle, like a bedtime storyteller"

**Speaking style**:
- "Fast-paced, enthusiastic sports commentator"
- "Slow, deliberate, thoughtful professor"
- "Casual, relaxed, conversational podcast host"

**Combined descriptions**:
- "Young woman, bright and cheerful, slight Valley Girl inflection, 20s"
- "Deep male voice, serious but warm, British accent, 40s"
- "Energetic child, about 10 years old, excited and curious"

### Batch Generation

```python
wavs, sr = model.generate_voice_design(
    text=["Hello!", "How are you?", "Goodbye!"],
    language=["English", "English", "English"],
    instruct="Friendly young female voice, upbeat",
)
```

### Limitations

- Voice consistency across multiple generations with the same instruction is not guaranteed. The model may produce slightly different voices each time.
- For consistent voice identity across a conversation, voice cloning (Base model) or preset speakers (CustomVoice model) are better choices.
- The VoiceDesign model is not currently integrated into agent-native-speech's `TTSEngine`. To use it, you would need to add a new profile type and model loading path.

### Integration Considerations

To add VoiceDesign support to agent-native-speech, you would need to:

1. Add a `"design"` profile type to `VoiceProfile` in `server/voice_profile.py`
2. Add a `VOICE_DESIGN_MODEL_ID` constant and loading logic in `server/tts_engine.py`
3. Add a synthesis dispatch case in `TTSEngine._synthesize_chunk()` for design profiles
4. Store the voice description instruction in `profile.json`

This is not currently implemented because voice design produces inconsistent voice identity across utterances, which is problematic for multi-turn conversations.

## CustomVoice Model (Preset Speakers)

The CustomVoice model provides 9 built-in speakers that can be controlled with style/emotion instructions.

### Model IDs

```
Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice   (full quality)
Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice   (lightweight)
```

### Available Speakers

| Speaker | Language | Description |
|---------|----------|-------------|
| **Ryan** | English | Male, versatile general-purpose voice |
| **Aiden** | English | Male, distinct from Ryan |
| **Vivian** | Chinese | Female |
| **Serena** | Chinese | Female |
| **Uncle_Fu** | Chinese | Male, mature voice |
| **Dylan** | Chinese | Male |
| **Eric** | Chinese | Male |
| **Ono_Anna** | Japanese | Female |
| **Sohee** | Korean | Female |

All speakers can synthesize text in any of the 10 supported languages, but they sound most natural in their native language.

### API Usage

```python
import torch
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    device_map="cuda:0",
    dtype=torch.bfloat16,
)

# Basic synthesis (no instruction)
wavs, sr = model.generate_custom_voice(
    text="Hello, how are you today?",
    language="English",
    speaker="Ryan",
)

# With emotion/style instruction
wavs, sr = model.generate_custom_voice(
    text="Oh wow, that's incredible news!",
    language="English",
    speaker="Ryan",
    instruct="Very excited and happy, speaking quickly with enthusiasm",
)
```

### Instruction Control Examples

The `instruct` parameter modifies how the preset speaker delivers the text:

**Emotions**:
```python
instruct="Very happy, cheerful"
instruct="Sad and melancholic, slow pace"
instruct="Angry, forceful delivery"
instruct="Surprised, with rising intonation"
instruct="Calm and soothing, like a meditation guide"
```

**Speaking styles**:
```python
instruct="Whispering softly"
instruct="Speaking loudly and clearly, as if addressing a crowd"
instruct="Reading a bedtime story to a child"
instruct="Professional news anchor delivery"
instruct="Casual, conversational, like talking to a friend"
```

**Combined**:
```python
instruct="Slightly nervous but trying to sound confident, moderate pace"
instruct="Warm and encouraging, like a supportive teacher"
```

### Batch Generation

```python
wavs, sr = model.generate_custom_voice(
    text=["Good morning!", "How can I help?", "Have a great day!"],
    language=["English", "English", "English"],
    speaker="Ryan",
    instruct="Friendly and professional",
)
```

### Integration with agent-native-speech

Preset speakers are built into agent-native-speech as the default voice type. The `VoiceProfileRegistry` in `server/voice_profile.py` automatically registers all 9 preset speakers.

**Using a preset voice**:

```json
// config.json
{
  "tts": {
    "backend": "local",
    "default_voice": "Ryan"
  }
}
```

**In the voice pool**:

```json
{
  "voice_pool": ["Ryan", "Aiden"]
}
```

Preset voices use the CustomVoice model. The engine loads it automatically when a preset voice is requested.

**Note**: agent-native-speech does not currently pass instruction text to `generate_custom_voice()`. The preset voices use only the speaker identity without emotion/style control. To add instruction support, you would need to:

1. Add an `instruct` field to `VoiceProfile`
2. Pass it through `TTSEngine._synthesize_chunk()` to the model call
3. Expose it via MCP tools or Discord commands

### Generation Parameters in agent-native-speech

Preset voices use slightly different generation parameters than cloned voices:

```python
PRESET_GENERATE_KWARGS = {
    "temperature": 0.7,        # More expressive than clone (0.3)
    "top_k": 30,               # Wider sampling
    "top_p": 0.9,              # More diverse output
    "repetition_penalty": 1.05, # Lighter penalty
    "subtalker_temperature": 0.7,
    "subtalker_top_k": 30,
    "subtalker_top_p": 0.9,
}
```

These parameters allow more expressiveness since preset speakers already have a stable voice identity.
