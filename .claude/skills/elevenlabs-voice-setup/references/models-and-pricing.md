# Models and Pricing

## TTS Models

### Model Comparison

| Model ID | Generation | Latency | Quality | Languages | Cost Multiplier |
|---|---|---|---|---|---|
| `eleven_v3` | Latest | Standard | Highest | 70+ | 1.0x |
| `eleven_multilingual_v2` | Previous | Standard | High | 29 | 1.0x |
| `eleven_flash_v2_5` | Flash | Ultra-low | Good | 32 | 0.5x |
| `eleven_turbo_v2_5` | Turbo | Low | Good | 32 | 0.5x |
| `eleven_monolingual_v1` | Legacy | Standard | Acceptable | 1 (English) | 1.0x |

### Model Details

#### eleven_v3

The latest and most capable model. Best for high-quality, expressive output.

- 70+ languages
- Most natural prosody and emotional range
- Dramatic delivery capabilities
- Best for: narration, character voices, high-quality agent voices
- Trade-off: higher latency than flash/turbo models

#### eleven_multilingual_v2

Previous-generation multilingual model. Solid general-purpose choice.

- 29 languages with good quality across all
- Stable, well-tested
- Best for: multilingual deployments, general purpose
- Trade-off: fewer languages than v3, less expressive

#### eleven_flash_v2_5

Optimized for minimal latency. Half the cost of standard models.

- Ultra-low latency (best for real-time)
- 32 languages
- 50% cheaper than v3/multilingual_v2
- Best for: real-time voice calls, interactive agents, high-volume use
- Trade-off: slightly less natural than v3
- **Recommended default for agent-native-speech** (used in config.json.example)

#### eleven_turbo_v2_5

Balance between speed and quality. Same pricing as flash.

- Low latency (slightly higher than flash, lower than standard)
- 32 languages
- 50% cheaper than v3/multilingual_v2
- Best for: real-time use where slightly more quality is wanted over flash
- Trade-off: marginally higher latency than flash

### Choosing a Model for agent-native-speech

For real-time Discord voice calls, latency is critical. Recommendations:

1. **Default choice:** `eleven_flash_v2_5` -- lowest latency, half cost, good quality
2. **Higher quality:** `eleven_turbo_v2_5` -- slightly more natural, small latency increase
3. **Maximum quality:** `eleven_v3` -- best expressiveness, but noticeable latency in voice calls
4. **Multilingual:** `eleven_multilingual_v2` or `eleven_v3` depending on language needs

Set the model in config.json:

```json
{
  "tts": {
    "elevenlabs": {
      "model_id": "eleven_flash_v2_5"
    }
  }
}
```

## STT Models

| Model ID | Description | Use Case |
|---|---|---|
| `scribe_v2` | Latest Scribe model | General transcription, best accuracy |

Set in config.json:

```json
{
  "stt": {
    "elevenlabs": {
      "model_id": "scribe_v2",
      "language_code": "eng"
    }
  }
}
```

Supported language codes: standard ISO 639-3 codes (e.g., `eng`, `fra`, `deu`, `spa`, `jpn`, `cmn`). Set to `null` or omit for auto-detection.

## Pricing

### Character-Based Quota

ElevenLabs pricing is based on **characters synthesized**, not API calls. Every character in the `text` parameter counts, including spaces and punctuation.

**Cost per character by model:**

| Model | Credits per character |
|---|---|
| `eleven_v3` | 1.0 |
| `eleven_multilingual_v2` | 1.0 |
| `eleven_flash_v2_5` | 0.5 |
| `eleven_turbo_v2_5` | 0.5 |

### Plan Tiers

| Plan | Monthly Characters | Concurrent Connections | Custom Voices | Price |
|---|---|---|---|---|
| Free | 10,000 | 2 | 3 | $0 |
| Starter | 30,000 | 3 | 10 | $5/mo |
| Creator | 100,000 | 5 | 30 | $22/mo |
| Pro | 500,000 | 10 | 160 | $99/mo |
| Scale | 2,000,000 | 15 | 160 | $330/mo |
| Enterprise | Custom | Custom | Custom | Custom |

**Note:** Pricing and limits are approximate and may change. Check https://elevenlabs.io/pricing for current details.

### Estimating Usage for Voice Calls

Rough estimates for agent-native-speech voice call usage:

- Average spoken sentence: ~80 characters
- Average agent response: ~200-400 characters
- 1 minute of agent speech: ~800-1200 characters
- 1 hour voice call (agent speaking ~50% of time): ~24,000-36,000 characters

**Per-model hourly cost (approximate):**

| Model | Characters/hour | Monthly limit reached at |
|---|---|---|
| Flash/Turbo (0.5x) | ~15,000 effective | ~13 hours (Free), ~67 hours (Pro) |
| v3/Multilingual (1.0x) | ~30,000 effective | ~6.5 hours (Free), ~33 hours (Pro) |

Using `eleven_flash_v2_5` effectively doubles your available voice call time.

## Rate Limits

### API Rate Limits

- Rate limits are per API key, not per voice or model
- Limits vary by plan tier
- 429 status code returned when exceeded
- Retry-After header indicates wait time

### Handling Rate Limits

```python
import time
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")

def synthesize_with_retry(text, voice_id, model_id, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.text_to_speech.convert(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                output_format="pcm_24000",
            )
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = 2 ** attempt  # exponential backoff
                print(f"Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
```

### Concurrent Connection Limits

Each plan has a maximum number of concurrent WebSocket/streaming connections:

| Plan | Max Concurrent |
|---|---|
| Free | 2 |
| Starter | 3 |
| Creator | 5 |
| Pro | 10 |
| Scale | 15 |
| Enterprise | Custom (15+) |

For agent-native-speech with multiple concurrent sessions, each active `synthesize_streamed()` call uses one connection. Monitor concurrent session count against your plan's limit.

**If you need 15+ concurrent voice sessions**, contact ElevenLabs for Enterprise pricing.

## Monitoring Usage

### Check quota via SDK

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="sk_...")
sub = client.user.get_subscription()

used_pct = (sub.character_count / sub.character_limit) * 100
print(f"Usage: {sub.character_count:,} / {sub.character_limit:,} ({used_pct:.1f}%)")
print(f"Resets: {sub.next_character_count_reset_unix}")
```

### Check quota via REST

```bash
curl -H "xi-api-key: sk_..." "https://api.elevenlabs.io/v1/user/subscription" \
  | python3 -m json.tool
```

### Usage Optimization Tips

1. **Use flash/turbo models** -- 50% fewer credits per character
2. **Keep responses concise** -- shorter agent responses save characters
3. **Cache common phrases** -- if your agents repeat greetings/farewells, cache the audio
4. **Preprocess text** -- the `tts_backend.preprocess()` function in agent-native-speech already strips code blocks and cleans text; ensure it removes unnecessary content before synthesis
5. **Monitor daily** -- set up alerts before hitting quota to avoid mid-call failures
